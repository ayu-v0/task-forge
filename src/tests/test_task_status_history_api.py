from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[2]
TEST_PREFIX = "task-status-history-test-"

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
            {"prefix": f"{TEST_PREFIX}%"},
        )


_cleanup_database()

from src.apps.api.app import app  # noqa: E402
from src.packages.core.db.models import EventLogORM, TaskBatchORM, TaskORM  # noqa: E402
from src.packages.core.task_state_machine import transition_task_status  # noqa: E402


client = TestClient(app)


def setup_function() -> None:
    _cleanup_database()


def teardown_function() -> None:
    _cleanup_database()


def _create_task(initial_status: str = "pending") -> str:
    engine = create_engine(_database_url())
    suffix = uuid.uuid4().hex[:8]
    with Session(engine) as session:
        batch = TaskBatchORM(
            title=f"{TEST_PREFIX}{suffix}",
            description="task status history batch",
            created_by="pytest",
            status="draft",
            total_tasks=1,
            metadata_json={"suite": "task-status-history"},
        )
        session.add(batch)
        session.flush()
        task = TaskORM(
            batch_id=batch.id,
            title=f"{TEST_PREFIX}task-{suffix}",
            description="task status history task",
            task_type="generate",
            priority="medium",
            status=initial_status,
            input_payload={},
            expected_output_schema={},
            assigned_agent_role=None,
            dependency_ids=[],
            retry_count=0,
        )
        session.add(task)
        session.commit()
        return task.id


def test_status_history_returns_status_changes_in_order() -> None:
    engine = create_engine(_database_url())
    task_id = _create_task("pending")

    with Session(engine) as session:
        task = session.get(TaskORM, task_id)
        assert task is not None
        transition_task_status(session, task, "queued", "routed to worker", "router")
        transition_task_status(session, task, "running", "worker started", "worker")
        transition_task_status(session, task, "success", "worker finished", "worker")
        session.commit()

    response = client.get(f"/tasks/{task_id}/status-history")
    assert response.status_code == 200
    payload = response.json()

    assert [item["new_status"] for item in payload] == ["queued", "running", "success"]
    assert payload[0]["task_id"] == task_id
    assert payload[0]["old_status"] == "pending"
    assert payload[0]["new_status"] == "queued"
    assert payload[0]["reason"] == "routed to worker"
    assert payload[0]["actor"] == "router"
    assert payload[0]["timestamp"]


def test_status_history_filters_out_non_status_events() -> None:
    engine = create_engine(_database_url())
    task_id = _create_task("pending")

    with Session(engine) as session:
        task = session.get(TaskORM, task_id)
        assert task is not None
        transition_task_status(session, task, "queued", "routed to worker", "router")
        session.add(
            EventLogORM(
                batch_id=task.batch_id,
                task_id=task.id,
                event_type="execution_run_started",
                event_status="running",
                message="worker started execution run",
                payload={"task_id": task.id, "source": "worker"},
            )
        )
        session.commit()

    response = client.get(f"/tasks/{task_id}/status-history")
    assert response.status_code == 200
    payload = response.json()

    assert len(payload) == 1
    assert payload[0]["new_status"] == "queued"


def test_status_history_returns_empty_list_when_no_transition_exists() -> None:
    task_id = _create_task("pending")

    response = client.get(f"/tasks/{task_id}/status-history")
    assert response.status_code == 200
    assert response.json() == []


def test_status_history_returns_404_for_unknown_task() -> None:
    response = client.get("/tasks/not_found/status-history")
    assert response.status_code == 404
    assert response.json()["detail"] == "Task not found"


def test_status_history_includes_cancel_transition_from_api() -> None:
    task_id = _create_task("pending")

    cancel_response = client.post(f"/tasks/{task_id}/cancel", json={"reason": "manual stop"})
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"

    response = client.get(f"/tasks/{task_id}/status-history")
    assert response.status_code == 200
    payload = response.json()

    assert len(payload) == 1
    assert payload[0]["old_status"] == "pending"
    assert payload[0]["new_status"] == "cancelled"
    assert payload[0]["reason"] == "manual stop"
    assert payload[0]["actor"] == "api"
