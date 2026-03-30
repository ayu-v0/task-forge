from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[2]
TEST_PREFIX = "cancel-test-"

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
            {"prefix": f"{TEST_PREFIX}%"},
        )
        conn.execute(
            text("DELETE FROM agent_roles WHERE role_name LIKE :prefix"),
            {"prefix": f"{TEST_PREFIX}%"},
        )


def _register_agent(client: TestClient, *, role_name: str, supported_task_types: list[str]) -> None:
    payload = {
        "role_name": role_name,
        "description": "cancel role",
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


def _batch_payload(task_type: str, suffix: str) -> dict:
    return {
        "title": f"{TEST_PREFIX}batch-{suffix}",
        "description": "cancel batch",
        "created_by": "pytest",
        "metadata": {"suite": "cancel"},
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
                "dependency_client_task_ids": [],
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
from src.apps.worker.executor import run_next_task  # noqa: E402
from src.apps.worker.registry import AgentRegistry, build_default_registry  # noqa: E402
from src.packages.core.db.models import ExecutionRunORM, TaskORM  # noqa: E402


client = TestClient(app)


class CancellableSlowAgent:
    def run(self, task: TaskORM, context) -> dict:
        for _ in range(20):
            if context.is_cancellation_requested():
                raise RuntimeError("agent observed cancellation request")
            time.sleep(0.05)
        return {"status": "ok", "task_id": task.id}


def setup_function() -> None:
    _cleanup_database()


def teardown_function() -> None:
    _cleanup_database()


def test_cancel_queued_task_prevents_execution() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(client, role_name="default_worker", supported_task_types=["generate"])

    response = client.post("/task-batches", json=_batch_payload("generate", suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]

    cancel_response = client.post(f"/tasks/{task_id}/cancel", json={"reason": "no longer needed"})
    assert cancel_response.status_code == 200
    payload = cancel_response.json()
    assert payload["status"] == "cancelled"
    assert payload["cancellation_requested"] is True

    engine = create_engine(_database_url())
    with Session(engine) as session:
        run = run_next_task(session, build_default_registry())
        assert run is not None
        assert run.task_id != task_id
        assert session.query(ExecutionRunORM).filter(ExecutionRunORM.task_id == task_id).count() == 0

    task_response = client.get(f"/tasks/{task_id}")
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "cancelled"


def test_cancel_blocked_task_stays_cancelled_after_dependency_success() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(client, role_name="default_worker", supported_task_types=["generate"])
    payload = {
        "title": f"{TEST_PREFIX}blocked-batch-{suffix}",
        "description": "blocked cancel batch",
        "created_by": "pytest",
        "metadata": {"suite": "cancel-blocked"},
        "tasks": [
            {
                "client_task_id": "task_1",
                "title": f"{TEST_PREFIX}blocked-{suffix}-1",
                "task_type": "generate",
                "priority": "medium",
                "input_payload": {"text": "a"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
            {
                "client_task_id": "task_2",
                "title": f"{TEST_PREFIX}blocked-{suffix}-2",
                "task_type": "generate",
                "priority": "medium",
                "input_payload": {"text": "b"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": ["task_1"],
            },
            {
                "client_task_id": "task_3",
                "title": f"{TEST_PREFIX}blocked-{suffix}-3",
                "task_type": "generate",
                "priority": "medium",
                "input_payload": {"text": "c"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
        ],
    }
    response = client.post("/task-batches", json=payload)
    assert response.status_code == 201
    dependent_task_id = response.json()["tasks"][1]["task_id"]

    cancel_response = client.post(f"/tasks/{dependent_task_id}/cancel", json={"reason": "stop blocked task"})
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"

    engine = create_engine(_database_url())
    registry = build_default_registry()
    with Session(engine) as session:
        first_run = run_next_task(session, registry)
        second_run = run_next_task(session, registry)
        assert first_run is not None
        assert second_run is not None
        assert {first_run.task_id, second_run.task_id}.isdisjoint({dependent_task_id})
        assert session.query(ExecutionRunORM).filter(ExecutionRunORM.task_id == dependent_task_id).count() == 0

    task_response = client.get(f"/tasks/{dependent_task_id}")
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "cancelled"


def test_cancel_running_task_marks_request_and_finishes_cancelled() -> None:
    suffix = uuid.uuid4().hex[:8]
    role_name = f"{TEST_PREFIX}slow-{suffix}"
    _register_agent(client, role_name=role_name, supported_task_types=[role_name])

    response = client.post("/task-batches", json=_batch_payload(role_name, suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]

    registry = AgentRegistry()
    registry.register(role_name, CancellableSlowAgent())
    engine = create_engine(_database_url())
    errors: list[Exception] = []

    def _run_task() -> None:
        try:
            with Session(engine) as session:
                run_next_task(session, registry)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    thread = threading.Thread(target=_run_task)
    thread.start()

    deadline = time.time() + 5
    while time.time() < deadline:
        task_response = client.get(f"/tasks/{task_id}")
        if task_response.status_code == 200 and task_response.json()["status"] == "running":
            break
        time.sleep(0.05)
    else:
        raise AssertionError("task never entered running state")

    cancel_response = client.post(f"/tasks/{task_id}/cancel", json={"reason": "user stop"})
    assert cancel_response.status_code == 200
    assert cancel_response.json()["cancellation_requested"] is True

    thread.join(timeout=10)
    assert not thread.is_alive()
    assert not errors

    final_task = client.get(f"/tasks/{task_id}")
    assert final_task.status_code == 200
    final_payload = final_task.json()
    assert final_payload["status"] == "cancelled"
    assert final_payload["cancellation_requested"] is True

    runs = client.get(f"/tasks/{task_id}/runs")
    assert runs.status_code == 200
    run_payload = runs.json()[0]
    assert run_payload["run_status"] == "cancelled"
    assert run_payload["cancel_reason"] == "user stop"

    events = client.get(f"/tasks/{task_id}/events")
    assert events.status_code == 200
    event_types = [item["event_type"] for item in events.json()]
    assert "task_cancellation_requested" in event_types
    assert "execution_run_cancelled" in event_types
    assert "task_cancellation_completed" in event_types


def test_cannot_cancel_success_or_failed_task() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(client, role_name="default_worker", supported_task_types=["generate"])

    response = client.post("/task-batches", json=_batch_payload("generate", suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]

    engine = create_engine(_database_url())
    registry = AgentRegistry()
    with Session(engine) as session:
        task = session.get(TaskORM, task_id)
        assert task is not None
        task.status = "success"
        session.commit()

    success_cancel = client.post(f"/tasks/{task_id}/cancel", json={"reason": "too late"})
    assert success_cancel.status_code == 409

    with Session(engine) as session:
        task = session.get(TaskORM, task_id)
        assert task is not None
        task.status = "failed"
        session.commit()

    failed_cancel = client.post(f"/tasks/{task_id}/cancel", json={"reason": "too late"})
    assert failed_cancel.status_code == 409
