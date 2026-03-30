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
TEST_PREFIX = "base-agent-test-"

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


def _register_agent(client: TestClient, *, role_name: str, supported_task_types: list[str]) -> None:
    payload = {
        "role_name": role_name,
        "description": "base agent role",
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


def _batch_payload(task_type: str, suffix: str, *, text_value: object = "hello") -> dict:
    return {
        "title": f"{TEST_PREFIX}batch-{suffix}",
        "description": "base agent batch",
        "created_by": "pytest",
        "metadata": {"suite": "base-agent"},
        "tasks": [
            {
                "client_task_id": "task_1",
                "title": f"{TEST_PREFIX}task-{suffix}-1",
                "task_type": task_type,
                "priority": "medium",
                "input_payload": {"text": text_value},
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
from src.apps.worker.registry import AgentRegistry  # noqa: E402
from src.packages.core.db.models import EventLogORM, TaskORM  # noqa: E402
from src.packages.sdk.base_agent import BaseAgent  # noqa: E402


client = TestClient(app)


class EchoBaseAgent(BaseAgent):
    def __init__(self, role_name: str) -> None:
        self.role_name = role_name
        self.capabilities = [f"task:{role_name}"]

    def validate_input(self, task: TaskORM) -> None:
        payload = task.input_payload or {}
        if not isinstance(payload.get("text"), str) or not payload["text"].strip():
            raise ValueError("input_payload.text must be a non-empty string")

    def run(self, task: TaskORM, context) -> dict:
        return {"status": "ok", "text": task.input_payload["text"], "run_id": context.run_id}


class InvalidOutputAgent(EchoBaseAgent):
    def run(self, task: TaskORM, context) -> dict:
        return "invalid"  # type: ignore[return-value]


class ErrorHookAgent(EchoBaseAgent):
    def __init__(self, role_name: str) -> None:
        super().__init__(role_name)
        self.errors: list[str] = []

    def run(self, task: TaskORM, context) -> dict:
        raise RuntimeError("boom from error hook agent")

    def on_error(self, task: TaskORM, context, exc: Exception) -> None:
        self.errors.append(f"{task.id}:{exc}")


class LegacyRunOnlyAgent:
    def run(self, task: TaskORM, context) -> dict:
        return {"status": "legacy-ok", "task_id": task.id}


class CancellableBaseAgent(EchoBaseAgent):
    def run(self, task: TaskORM, context) -> dict:
        for _ in range(20):
            if context.is_cancellation_requested():
                raise RuntimeError("base agent observed cancellation request")
            time.sleep(0.05)
        return {"status": "ok", "task_id": task.id}


def setup_function() -> None:
    _cleanup_database()


def teardown_function() -> None:
    _cleanup_database()


def test_base_agent_executes_successfully() -> None:
    suffix = uuid.uuid4().hex[:8]
    role_name = f"{TEST_PREFIX}echo-{suffix}"
    _register_agent(client, role_name=role_name, supported_task_types=[role_name])
    response = client.post("/task-batches", json=_batch_payload(role_name, suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]

    registry = AgentRegistry()
    registry.register(role_name, EchoBaseAgent(role_name))

    engine = create_engine(_database_url())
    with Session(engine) as session:
        run = run_next_task(session, registry)
        assert run is not None
        assert run.run_status == "success"
        assert run.output_snapshot["text"] == "hello"

    task_response = client.get(f"/tasks/{task_id}")
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "success"


def test_base_agent_validate_input_failure_marks_task_failed() -> None:
    suffix = uuid.uuid4().hex[:8]
    role_name = f"{TEST_PREFIX}invalid-input-{suffix}"
    _register_agent(client, role_name=role_name, supported_task_types=[role_name])
    response = client.post("/task-batches", json=_batch_payload(role_name, suffix, text_value=""))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]

    registry = AgentRegistry()
    registry.register(role_name, EchoBaseAgent(role_name))

    engine = create_engine(_database_url())
    with Session(engine) as session:
        run = run_next_task(session, registry)
        assert run is not None
        assert run.run_status == "failed"
        assert "input_payload.text must be a non-empty string" in run.error_message

    task_response = client.get(f"/tasks/{task_id}")
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "failed"


def test_base_agent_validate_output_failure_marks_task_failed() -> None:
    suffix = uuid.uuid4().hex[:8]
    role_name = f"{TEST_PREFIX}invalid-output-{suffix}"
    _register_agent(client, role_name=role_name, supported_task_types=[role_name])
    response = client.post("/task-batches", json=_batch_payload(role_name, suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]

    registry = AgentRegistry()
    registry.register(role_name, InvalidOutputAgent(role_name))

    engine = create_engine(_database_url())
    with Session(engine) as session:
        run = run_next_task(session, registry)
        assert run is not None
        assert run.run_status == "failed"
        assert "Agent output must be a dict" in run.error_message

    task_response = client.get(f"/tasks/{task_id}")
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "failed"


def test_base_agent_on_error_is_called_before_failure_propagation() -> None:
    suffix = uuid.uuid4().hex[:8]
    role_name = f"{TEST_PREFIX}hook-{suffix}"
    _register_agent(client, role_name=role_name, supported_task_types=[role_name])
    response = client.post("/task-batches", json=_batch_payload(role_name, suffix))
    assert response.status_code == 201

    agent = ErrorHookAgent(role_name)
    registry = AgentRegistry()
    registry.register(role_name, agent)

    engine = create_engine(_database_url())
    with Session(engine) as session:
        run = run_next_task(session, registry)
        assert run is not None
        assert run.run_status == "failed"
        assert "boom from error hook agent" in run.error_message

    assert len(agent.errors) == 1
    assert "boom from error hook agent" in agent.errors[0]


def test_registry_rejects_role_name_mismatch() -> None:
    registry = AgentRegistry()
    agent = EchoBaseAgent("declared-role")

    try:
        registry.register("other-role", agent)
    except ValueError as exc:
        assert "Agent role_name mismatch" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected role name mismatch to fail")


def test_legacy_run_only_agent_still_works() -> None:
    suffix = uuid.uuid4().hex[:8]
    role_name = f"{TEST_PREFIX}legacy-{suffix}"
    _register_agent(client, role_name=role_name, supported_task_types=[role_name])
    response = client.post("/task-batches", json=_batch_payload(role_name, suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]

    registry = AgentRegistry()
    registry.register(role_name, LegacyRunOnlyAgent())

    engine = create_engine(_database_url())
    with Session(engine) as session:
        run = run_next_task(session, registry)
        assert run is not None
        assert run.run_status == "success"
        assert run.output_snapshot["status"] == "legacy-ok"

    task_response = client.get(f"/tasks/{task_id}")
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "success"


def test_base_agent_supports_cancellation_context() -> None:
    suffix = uuid.uuid4().hex[:8]
    role_name = f"{TEST_PREFIX}cancel-{suffix}"
    _register_agent(client, role_name=role_name, supported_task_types=[role_name])
    response = client.post("/task-batches", json=_batch_payload(role_name, suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]

    registry = AgentRegistry()
    registry.register(role_name, CancellableBaseAgent(role_name))
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

    cancel_response = client.post(f"/tasks/{task_id}/cancel", json={"reason": "cancel base agent"})
    assert cancel_response.status_code == 200

    thread.join(timeout=10)
    assert not thread.is_alive()
    assert not errors

    runs_response = client.get(f"/tasks/{task_id}/runs")
    assert runs_response.status_code == 200
    assert runs_response.json()[0]["run_status"] == "cancelled"

    with Session(engine) as session:
        events = (
            session.query(EventLogORM)
            .filter(EventLogORM.task_id == task_id)
            .order_by(EventLogORM.created_at.asc())
            .all()
        )
    assert any(event.event_type == "execution_run_cancelled" for event in events)
