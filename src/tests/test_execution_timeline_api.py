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
TEST_PREFIX = "execution-timeline-test-"

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
        conn.execute(text("DELETE FROM task_batches WHERE title LIKE :prefix"), {"prefix": f"{TEST_PREFIX}%"})
        conn.execute(text("DELETE FROM agent_roles WHERE role_name LIKE :prefix"), {"prefix": f"{TEST_PREFIX}%"})


def _register_agent(client: TestClient, *, role_name: str, supported_task_types: list[str]) -> dict:
    payload = {
        "role_name": role_name,
        "description": "timeline role",
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
    assert response.status_code == 201
    return response.json()


def _batch_payload(task_type: str, suffix: str) -> dict:
    return {
        "title": f"{TEST_PREFIX}batch-{suffix}",
        "description": "timeline batch",
        "created_by": "pytest",
        "metadata": {"suite": "execution-timeline"},
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
from src.packages.core.db.models import AssignmentORM, EventLogORM, ExecutionRunORM, TaskORM  # noqa: E402
from src.packages.core.task_state_machine import transition_task_status  # noqa: E402


client = TestClient(app)


def setup_function() -> None:
    _cleanup_database()


def teardown_function() -> None:
    _cleanup_database()


def test_task_timeline_rebuilds_full_lifecycle_with_retry() -> None:
    suffix = uuid.uuid4().hex[:8]
    role = _register_agent(client, role_name=f"{TEST_PREFIX}worker-{suffix}", supported_task_types=["generate"])
    response = client.post("/task-batches", json=_batch_payload("generate", suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]

    engine = create_engine(_database_url())
    with Session(engine) as session:
        task = session.get(TaskORM, task_id)
        assert task is not None
        assignment = session.query(AssignmentORM).filter(AssignmentORM.task_id == task.id).first()
        assert assignment is not None
        first_run = ExecutionRunORM(
            task_id=task.id,
            agent_role_id=assignment.agent_role_id,
            run_status="failed",
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc) + timedelta(seconds=1),
            error_message="first attempt failed",
            input_snapshot={"attempt": 1},
            output_snapshot={},
        )
        session.add(first_run)
        session.flush()
        session.add(
            EventLogORM(
                batch_id=task.batch_id,
                task_id=task.id,
                run_id=first_run.id,
                event_type="execution_run_started",
                event_status="running",
                message="worker started execution run",
                payload={"task_id": task.id, "run_id": first_run.id, "source": "worker"},
            )
        )
        transition_task_status(session, task, "running", "worker claimed queued task", "worker", run_id=first_run.id)
        transition_task_status(session, task, "failed", "worker execution failed", "worker", run_id=first_run.id)
        transition_task_status(session, task, "queued", "retry requested", "review")
        second_run = ExecutionRunORM(
            task_id=task.id,
            agent_role_id=assignment.agent_role_id,
            run_status="success",
            started_at=datetime.now(timezone.utc) + timedelta(seconds=2),
            finished_at=datetime.now(timezone.utc) + timedelta(seconds=3),
            input_snapshot={"attempt": 2},
            output_snapshot={"artifact": "report"},
        )
        session.add(second_run)
        session.flush()
        session.add(
            EventLogORM(
                batch_id=task.batch_id,
                task_id=task.id,
                run_id=second_run.id,
                event_type="execution_run_finished",
                event_status="success",
                message="worker completed execution run",
                payload={"task_id": task.id, "run_id": second_run.id, "source": "worker"},
            )
        )
        transition_task_status(session, task, "running", "worker claimed queued task", "worker", run_id=second_run.id)
        transition_task_status(session, task, "success", "worker finished task successfully", "worker", run_id=second_run.id)
        session.commit()

    timeline_response = client.get(f"/tasks/{task_id}/timeline")
    assert timeline_response.status_code == 200
    payload = timeline_response.json()
    stages = [item["stage"] for item in payload["items"]]
    assert stages[0] == "created"
    assert "routed" in stages
    assert "queued" in stages
    assert "running" in stages
    assert "failed" in stages
    assert "retry" in stages
    assert "completed" in stages


def test_task_timeline_includes_review_stage_and_batch_timeline_merges_task_events() -> None:
    suffix = uuid.uuid4().hex[:8]
    response = client.post("/task-batches", json=_batch_payload("unmatched_type", suffix))
    assert response.status_code == 201
    batch_id = response.json()["batch_id"]
    task_id = response.json()["tasks"][0]["task_id"]

    task_timeline = client.get(f"/tasks/{task_id}/timeline")
    assert task_timeline.status_code == 200
    task_stages = [item["stage"] for item in task_timeline.json()["items"]]
    assert "review" in task_stages

    batch_timeline = client.get(f"/task-batches/{batch_id}/timeline")
    assert batch_timeline.status_code == 200
    items = batch_timeline.json()["items"]
    assert items[0]["title"] == "Batch created"
    assert any(item["task_id"] == task_id for item in items)
    assert any(item["stage"] == "review" for item in items)


def test_timeline_endpoints_return_404_for_unknown_resources() -> None:
    assert client.get("/tasks/not_found/timeline").status_code == 404
    assert client.get("/task-batches/not_found/timeline").status_code == 404
