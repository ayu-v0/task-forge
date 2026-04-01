from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[2]
TEST_PREFIX = "batch-detail-test-"

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
        conn.execute(text("DELETE FROM task_batches"))
        conn.execute(text("DELETE FROM agent_roles"))


def _register_agent(client: TestClient, *, role_name: str, supported_task_types: list[str]) -> dict:
    payload = {
        "role_name": role_name,
        "description": "batch detail role",
        "capabilities": [f"task:{role_name}"],
        "capability_declaration": {
            "supported_task_types": supported_task_types,
            "input_requirements": {"properties": {"text": {"type": "string"}}},
            "output_contract": {"type": "object"},
            "supports_concurrency": True,
            "allows_auto_retry": False,
        },
        "input_schema": {},
        "output_schema": {},
        "timeout_seconds": 300,
        "max_retries": 0,
        "enabled": True,
        "version": "1.0.0",
    }
    response = client.post("/agents/register", json=payload)
    assert response.status_code in {201, 400}
    if response.status_code == 400:
        roles_response = client.get("/agents")
        assert roles_response.status_code == 200
        return next(role for role in roles_response.json() if role["role_name"] == role_name)
    return response.json()


def _batch_payload(task_type: str, suffix: str) -> dict:
    return {
        "title": f"{TEST_PREFIX}batch-{suffix}",
        "description": "batch detail batch",
        "created_by": "pytest",
        "metadata": {"suite": "batch-detail"},
        "tasks": [
            {
                "client_task_id": "task_1",
                "title": f"{TEST_PREFIX}task-{suffix}-1",
                "task_type": task_type,
                "priority": "medium",
                "input_payload": {"text": "hello"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
            {
                "client_task_id": "task_2",
                "title": f"{TEST_PREFIX}task-{suffix}-2",
                "task_type": task_type,
                "priority": "medium",
                "input_payload": {"text": "world"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": ["task_1"],
            },
            {
                "client_task_id": "task_3",
                "title": f"{TEST_PREFIX}task-{suffix}-3",
                "task_type": task_type,
                "priority": "medium",
                "input_payload": {"text": "!"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
        ],
    }


_cleanup_database()

from src.apps.api.app import app  # noqa: E402
from src.packages.core.db.models import AssignmentORM, ExecutionRunORM, TaskORM  # noqa: E402


client = TestClient(app)


def setup_function() -> None:
    _cleanup_database()


def teardown_function() -> None:
    _cleanup_database()


def test_batch_summary_includes_dependency_ids_for_detail_view() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(client, role_name="default_worker", supported_task_types=[])
    response = client.post("/task-batches", json=_batch_payload("unmatched_type", suffix))
    assert response.status_code == 201
    batch_id = response.json()["batch_id"]
    first_task_id = response.json()["tasks"][0]["task_id"]
    second_task_id = response.json()["tasks"][1]["task_id"]

    summary_response = client.get(f"/task-batches/{batch_id}/summary")
    assert summary_response.status_code == 200
    tasks = summary_response.json()["tasks"]
    second_task = next(item for item in tasks if item["task_id"] == second_task_id)
    assert second_task["dependency_ids"] == [first_task_id]


def test_console_batch_detail_page_is_accessible() -> None:
    response = client.get("/console/batches/sample-batch-id")
    assert response.status_code == 200
    assert "Batch Detail" in response.text
    assert "/console/assets/batch-detail.js" in response.text


def test_batch_detail_page_can_link_to_run_detail_when_latest_run_exists() -> None:
    response = client.get("/console/assets/batch-detail.js")
    assert response.status_code == 200
    assert "View run detail" in response.text


def test_batch_detail_summary_supports_mixed_risk_sections() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(client, role_name="default_worker", supported_task_types=[])
    response = client.post("/task-batches", json=_batch_payload("unmatched_type", suffix))
    assert response.status_code == 201
    batch_id = response.json()["batch_id"]
    task_ids = [task["task_id"] for task in response.json()["tasks"]]

    engine = create_engine(_database_url())
    with Session(engine) as session:
        first_task = session.get(TaskORM, task_ids[0])
        second_task = session.get(TaskORM, task_ids[1])
        third_task = session.get(TaskORM, task_ids[2])
        assert first_task is not None and second_task is not None and third_task is not None

        first_assignment = session.query(AssignmentORM).filter(AssignmentORM.task_id == first_task.id).first()
        assert first_assignment is not None

        first_task.status = "failed"
        second_task.status = "blocked"
        third_task.status = "needs_review"

        started_at = datetime.now(timezone.utc)
        session.add(
            ExecutionRunORM(
                task_id=first_task.id,
                agent_role_id=first_assignment.agent_role_id,
                run_status="failed",
                started_at=started_at,
                finished_at=started_at + timedelta(seconds=1),
                error_message="detail page should highlight this failure",
                output_snapshot={"step": "compile"},
            )
        )
        session.commit()

    summary_response = client.get(f"/task-batches/{batch_id}/summary")
    assert summary_response.status_code == 200
    payload = summary_response.json()
    assert payload["derived_status"] == "needs_review"
    assert payload["counts"]["failed_count"] == 1
    assert payload["counts"]["blocked_count"] == 1
    assert payload["counts"]["needs_review_count"] == 1

    failed_task = next(item for item in payload["tasks"] if item["task_id"] == task_ids[0])
    blocked_task = next(item for item in payload["tasks"] if item["task_id"] == task_ids[1])
    review_task = next(item for item in payload["tasks"] if item["task_id"] == task_ids[2])

    assert failed_task["error_message"] == "detail page should highlight this failure"
    assert blocked_task["dependency_ids"] == [task_ids[0]]
    assert review_task["status"] == "needs_review"
