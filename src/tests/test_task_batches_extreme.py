from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text


TEST_TITLE_PREFIX = "extreme-task-batch-"
ROOT = Path(__file__).resolve().parents[2]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")
    return database_url


def _cleanup_database() -> None:
    engine = create_engine(_database_url())
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM task_batches WHERE title LIKE :prefix"),
            {"prefix": f"{TEST_TITLE_PREFIX}%"},
        )


def _payload(task_count: int = 3) -> dict:
    suffix = uuid.uuid4().hex[:8]
    tasks = []
    for index in range(task_count):
        client_task_id = f"task_{index + 1}"
        dependency_ids = []
        if index == 1:
            dependency_ids = ["task_1"]
        elif index > 1:
            dependency_ids = [f"task_{index}"]

        tasks.append(
            {
                "client_task_id": client_task_id,
                "title": f"extreme-task-{suffix}-{index + 1}",
                "description": f"task {index + 1}",
                "task_type": "generate",
                "priority": "medium",
                "input_payload": {"step": index + 1},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": dependency_ids,
            }
        )

    return {
        "title": f"{TEST_TITLE_PREFIX}{suffix}",
        "description": "extreme case test",
        "created_by": "pytest",
        "metadata": {"suite": "extreme"},
        "tasks": tasks,
    }


_cleanup_database()

from src.apps.api.app import app  # noqa: E402


client = TestClient(app)


def setup_function() -> None:
    _cleanup_database()


def teardown_function() -> None:
    _cleanup_database()


def test_submit_maximum_20_tasks() -> None:
    response = client.post("/task-batches", json=_payload(task_count=20))

    assert response.status_code == 201
    body = response.json()
    assert len(body["tasks"]) == 20
    assert all(task["status"] == "waiting_review" for task in body["tasks"])
    assert all(task["needs_review"] is True for task in body["tasks"])
    batch_response = client.get(f"/task-batches/{body['batch_id']}")
    assert batch_response.status_code == 200
    batch_body = batch_response.json()
    assert batch_body["id"] == body["batch_id"]
    assert batch_body["total_tasks"] == 20
    assert batch_body["status"] == "draft"


def test_submit_and_fetch_batch_with_dependencies() -> None:
    response = client.post("/task-batches", json=_payload(task_count=3))

    assert response.status_code == 201
    body = response.json()
    assert len(body["tasks"][0]["dependency_ids"]) == 0
    assert len(body["tasks"][1]["dependency_ids"]) == 1
    assert len(body["tasks"][2]["dependency_ids"]) == 1

    batch_response = client.get(f"/task-batches/{body['batch_id']}")
    assert batch_response.status_code == 200
    batch_body = batch_response.json()
    assert batch_body["id"] == body["batch_id"]
    assert batch_body["total_tasks"] == 3
    assert batch_body["metadata"]["suite"] == "extreme"


def test_submit_rejects_duplicate_client_task_ids() -> None:
    payload = _payload()
    payload["tasks"][1]["client_task_id"] = payload["tasks"][0]["client_task_id"]

    response = client.post("/task-batches", json=payload)

    assert response.status_code == 400
    assert "Duplicate client_task_id values" in response.json()["detail"]


def test_submit_rejects_self_dependency() -> None:
    payload = _payload()
    payload["tasks"][0]["dependency_client_task_ids"] = ["task_1"]

    response = client.post("/task-batches", json=payload)

    assert response.status_code == 400
    assert response.json()["detail"] == "Task task_1 cannot depend on itself"


def test_submit_rejects_unknown_dependency() -> None:
    payload = _payload()
    payload["tasks"][2]["dependency_client_task_ids"] = ["task_9"]

    response = client.post("/task-batches", json=payload)

    assert response.status_code == 400
    assert response.json()["detail"] == "Task task_3 depends on unknown task task_9"


def test_submit_rejects_circular_dependency() -> None:
    payload = _payload()
    payload["tasks"][0]["dependency_client_task_ids"] = ["task_3"]
    payload["tasks"][1]["dependency_client_task_ids"] = ["task_1"]
    payload["tasks"][2]["dependency_client_task_ids"] = ["task_2"]

    response = client.post("/task-batches", json=payload)

    assert response.status_code == 400
    assert response.json()["detail"] == "Circular dependency detected in submitted tasks"


def test_submit_rolls_back_on_invalid_dependency() -> None:
    payload = _payload()
    payload["tasks"][1]["dependency_client_task_ids"] = ["missing_task"]

    response = client.post("/task-batches", json=payload)

    assert response.status_code == 400

    engine = create_engine(_database_url())
    with engine.connect() as conn:
        batch_count = conn.execute(
            text("SELECT count(*) FROM task_batches WHERE title = :title"),
            {"title": payload["title"]},
        ).scalar_one()
        task_count = conn.execute(
            text("SELECT count(*) FROM tasks WHERE title LIKE :prefix"),
            {"prefix": f"extreme-task-{payload['title'].split('-')[-1]}%"},
        ).scalar_one()

    assert batch_count == 0
    assert task_count == 0
