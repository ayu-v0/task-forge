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
TEST_PREFIX = "summary-test-"

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
        "description": "summary role",
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
        assert response.json()["detail"] == f"Agent role {role_name} already exists"
        roles_response = client.get("/agents")
        assert roles_response.status_code == 200
        return next(role for role in roles_response.json() if role["role_name"] == role_name)
    return response.json()


def _batch_payload(task_type: str, suffix: str) -> dict:
    return {
        "title": f"{TEST_PREFIX}batch-{suffix}",
        "description": "summary batch",
        "created_by": "pytest",
        "metadata": {"suite": "summary"},
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
from src.apps.worker.executor import claim_next_task, run_next_task  # noqa: E402
from src.apps.worker.registry import AgentRegistry  # noqa: E402
from src.packages.core.db.models import ArtifactORM, AssignmentORM, ExecutionRunORM, TaskORM  # noqa: E402


client = TestClient(app)


class SlowAgent:
    def run(self, task: TaskORM, context) -> dict:
        import time

        time.sleep(0.2)
        return {"status": "ok", "task_id": task.id}


class SummaryAgent:
    def run(self, task: TaskORM, context) -> dict:
        return {"status": "ok", "task_id": task.id}


def setup_function() -> None:
    _cleanup_database()


def teardown_function() -> None:
    _cleanup_database()


def test_summary_returns_success_counts_and_progress() -> None:
    suffix = uuid.uuid4().hex[:8]
    role_name = f"{TEST_PREFIX}worker-{suffix}"
    _register_agent(client, role_name=role_name, supported_task_types=["generate"])

    response = client.post("/task-batches", json=_batch_payload("generate", suffix))
    assert response.status_code == 201
    batch_id = response.json()["batch_id"]

    engine = create_engine(_database_url())
    registry = AgentRegistry()
    registry.register(role_name, SummaryAgent())
    with Session(engine) as session:
        first_run = run_next_task(session, registry)
        second_run = run_next_task(session, registry)
        third_run = run_next_task(session, registry)
        assert first_run is not None
        assert second_run is not None
        assert third_run is not None

    summary_response = client.get(f"/task-batches/{batch_id}/summary")
    assert summary_response.status_code == 200
    payload = summary_response.json()
    assert payload["derived_status"] == "success"
    assert payload["counts"]["success_count"] == 3
    assert payload["progress"]["completed_count"] == 3
    assert payload["progress"]["progress_percent"] == 100.0
    assert len(payload["tasks"]) == 3


def test_summary_returns_needs_review_status_for_unrouted_batch() -> None:
    suffix = uuid.uuid4().hex[:8]
    response = client.post("/task-batches", json=_batch_payload("no_match", suffix))
    assert response.status_code == 201
    batch_id = response.json()["batch_id"]

    summary_response = client.get(f"/task-batches/{batch_id}/summary")
    assert summary_response.status_code == 200
    payload = summary_response.json()
    assert payload["derived_status"] == "needs_review"
    assert payload["counts"]["needs_review_count"] == 3
    assert payload["progress"]["completed_count"] == 0


def test_summary_returns_running_status_when_task_is_in_progress() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(client, role_name="default_worker", supported_task_types=[])
    response = client.post("/task-batches", json=_batch_payload("unmatched_type", suffix))
    assert response.status_code == 201
    batch_id = response.json()["batch_id"]

    engine = create_engine(_database_url())
    with Session(engine) as session:
        claimed = claim_next_task(session)
        assert claimed is not None
        session.commit()

    summary_response = client.get(f"/task-batches/{batch_id}/summary")
    assert summary_response.status_code == 200
    payload = summary_response.json()
    assert payload["derived_status"] == "running"
    assert payload["counts"]["running_count"] == 1


def test_summary_returns_partially_failed_for_mixed_terminal_states() -> None:
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
        first_task.status = "success"
        second_task.status = "failed"
        third_task.status = "cancelled"
        session.commit()

    summary_response = client.get(f"/task-batches/{batch_id}/summary")
    assert summary_response.status_code == 200
    payload = summary_response.json()
    assert payload["derived_status"] == "partially_failed"
    assert payload["counts"]["success_count"] == 1
    assert payload["counts"]["failed_count"] == 1
    assert payload["counts"]["cancelled_count"] == 1


def test_summary_aggregates_latest_run_and_artifacts() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(client, role_name="default_worker", supported_task_types=[])
    response = client.post("/task-batches", json=_batch_payload("unmatched_type", suffix))
    assert response.status_code == 201
    batch_id = response.json()["batch_id"]
    task_id = response.json()["tasks"][0]["task_id"]

    engine = create_engine(_database_url())
    with Session(engine) as session:
        task = session.get(TaskORM, task_id)
        assert task is not None
        assignment = session.query(AssignmentORM).filter(AssignmentORM.task_id == task.id).first()
        assert assignment is not None
        first_started_at = datetime.now(timezone.utc)
        first_run = ExecutionRunORM(
            task_id=task.id,
            agent_role_id=assignment.agent_role_id,
            run_status="failed",
            started_at=first_started_at,
            output_snapshot={"version": 1},
            error_message="first failure",
        )
        second_run = ExecutionRunORM(
            task_id=task.id,
            agent_role_id=assignment.agent_role_id,
            run_status="success",
            started_at=first_started_at + timedelta(seconds=1),
            output_snapshot={"version": 2},
        )
        session.add(first_run)
        session.flush()
        session.add(second_run)
        session.flush()
        session.add(
            ArtifactORM(
                task_id=task.id,
                run_id=second_run.id,
                artifact_type="report",
                uri="memory://report.json",
                content_type="application/json",
            )
        )
        session.commit()

    summary_response = client.get(f"/task-batches/{batch_id}/summary")
    assert summary_response.status_code == 200
    payload = summary_response.json()
    task_summary = next(item for item in payload["tasks"] if item["task_id"] == task_id)
    assert task_summary["latest_run_status"] == "success"
    assert task_summary["output_snapshot"] == {"version": 2}
    assert task_summary["artifact_count"] == 1
    assert len(payload["artifacts"]) == 1
