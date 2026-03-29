from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[2]
WORKER_PREFIX = "worker-test-"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        env_file = ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("DATABASE_URL="):
                    database_url = line.split("=", 1)[1].strip()
                    break
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")
    return database_url


def _cleanup_database() -> None:
    engine = create_engine(_database_url())
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM task_batches WHERE title LIKE :prefix"),
            {"prefix": f"{WORKER_PREFIX}%"},
        )
        conn.execute(
            text(
                "DELETE FROM agent_roles "
                "WHERE role_name IN ('default_worker', 'failing_worker', 'echo_worker')"
            )
        )


_cleanup_database()

from src.apps.api.app import app  # noqa: E402
from src.apps.worker.service import WorkerService  # noqa: E402


client = TestClient(app)


def setup_function() -> None:
    _cleanup_database()


def teardown_function() -> None:
    _cleanup_database()


def _register_agent(
    *,
    role_name: str,
    capabilities: list[str],
    supported_task_types: list[str],
) -> dict:
    payload = {
        "role_name": role_name,
        "description": "worker test role",
        "capabilities": capabilities,
        "capability_declaration": {
            "supported_task_types": supported_task_types,
            "input_requirements": {"properties": {"text": {"type": "string"}}},
            "output_contract": {"type": "object"},
            "supports_concurrency": False,
            "allows_auto_retry": False,
        },
        "input_schema": {},
        "output_schema": {},
        "timeout_seconds": 300,
        "max_retries": 1,
        "enabled": True,
        "version": "1.0.0",
    }
    response = client.post("/agents/register", json=payload)
    assert response.status_code == 201
    return response.json()


def _submit_batch(*, task_type: str, title_suffix: str) -> dict:
    payload = {
        "title": f"{WORKER_PREFIX}batch-{title_suffix}",
        "description": "worker execution batch",
        "created_by": "pytest",
        "metadata": {"suite": "worker"},
        "tasks": [
            {
                "client_task_id": "task_1",
                "title": f"{WORKER_PREFIX}task-{title_suffix}-1",
                "task_type": task_type,
                "priority": "medium",
                "input_payload": {"text": "hello"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
            {
                "client_task_id": "task_2",
                "title": f"{WORKER_PREFIX}task-{title_suffix}-2",
                "task_type": task_type,
                "priority": "medium",
                "input_payload": {"text": "world"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
            {
                "client_task_id": "task_3",
                "title": f"{WORKER_PREFIX}task-{title_suffix}-3",
                "task_type": task_type,
                "priority": "medium",
                "input_payload": {"text": "!"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
        ],
    }
    response = client.post("/task-batches", json=payload)
    assert response.status_code == 201
    return response.json()


def test_worker_executes_queued_task_to_success() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(
        role_name="echo_worker",
        capabilities=["task:echo_task"],
        supported_task_types=["echo_task"],
    )
    created = _submit_batch(task_type="echo_task", title_suffix=suffix)

    engine = create_engine(_database_url())
    with Session(engine) as session:
        worker = WorkerService(session)
        run = worker.run_once()
        assert run is not None
        run_id = run.id
        task_id = run.task_id

    task_response = client.get(f"/tasks/{task_id}")
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "success"

    run_response = client.get(f"/runs/{run_id}")
    assert run_response.status_code == 200
    run_body = run_response.json()
    assert run_body["run_status"] == "succeeded"
    assert run_body["output_snapshot"]["status"] == "ok"
    assert run_body["output_snapshot"]["task_id"] == task_id
    assert run_body["output_snapshot"]["echo"]["text"] in {"hello", "world", "!"}
    assert run_body["latency_ms"] is not None

    events_response = client.get(f"/tasks/{task_id}/events")
    assert events_response.status_code == 200
    event_statuses = [event["event_status"] for event in events_response.json() if event["event_type"] == "task_status_changed"]
    assert event_statuses == ["queued", "running", "success"]

    remaining_task_ids = [task["task_id"] for task in created["tasks"] if task["task_id"] != task_id]
    assert len(remaining_task_ids) == 2


def test_worker_marks_task_failed_and_persists_error() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(
        role_name="failing_worker",
        capabilities=["task:fail_task"],
        supported_task_types=["fail_task"],
    )
    _submit_batch(task_type="fail_task", title_suffix=suffix)

    engine = create_engine(_database_url())
    with Session(engine) as session:
        worker = WorkerService(session)
        run = worker.run_once()
        assert run is not None
        run_id = run.id
        task_id = run.task_id

    task_response = client.get(f"/tasks/{task_id}")
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "failed"

    run_response = client.get(f"/runs/{run_id}")
    assert run_response.status_code == 200
    run_body = run_response.json()
    assert run_body["run_status"] == "failed"
    assert "Agent execution failed" in run_body["error_message"]
    assert run_body["output_snapshot"] == {}

    events_response = client.get(f"/tasks/{task_id}/events")
    assert events_response.status_code == 200
    event_statuses = [event["event_status"] for event in events_response.json() if event["event_type"] == "task_status_changed"]
    assert event_statuses == ["queued", "running", "failed"]


def test_run_once_returns_none_when_queue_is_empty() -> None:
    engine = create_engine(_database_url())
    with Session(engine) as session:
        worker = WorkerService(session)
        run = worker.run_once()
        assert run is None

    with engine.connect() as conn:
        run_count = conn.execute(
            text(
                "SELECT count(*) "
                "FROM execution_runs r "
                "JOIN tasks t ON r.task_id = t.id "
                "WHERE t.title LIKE :prefix"
            ),
            {"prefix": f"{WORKER_PREFIX}%"},
        ).scalar_one()
    assert run_count == 0


def test_worker_does_not_consume_same_task_twice() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(
        role_name="echo_worker",
        capabilities=["task:echo_task"],
        supported_task_types=["echo_task"],
    )
    _submit_batch(task_type="echo_task", title_suffix=suffix)

    engine = create_engine(_database_url())
    with Session(engine) as first_session:
        worker = WorkerService(first_session)
        first_run = worker.run_once()
        assert first_run is not None
        first_task_id = first_run.task_id

    with Session(engine) as second_session:
        worker = WorkerService(second_session)
        second_run = worker.run_once()
        assert second_run is not None
        assert second_run.task_id != first_task_id

    with engine.connect() as conn:
        remaining_queued = conn.execute(
            text("SELECT count(*) FROM tasks WHERE status = 'queued' AND title LIKE :prefix"),
            {"prefix": f"{WORKER_PREFIX}%"},
        ).scalar_one()
    assert remaining_queued == 1
