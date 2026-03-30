from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[2]
TEST_PREFIX = "worker-test-"

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
        conn.execute(
            text("DELETE FROM agent_roles WHERE role_name LIKE :prefix OR role_name = 'default_worker'"),
            {"prefix": f"{TEST_PREFIX}%"},
        )


def _batch_payload(task_type: str, suffix: str) -> dict:
    return {
        "title": f"{TEST_PREFIX}batch-{suffix}",
        "description": "worker batch",
        "created_by": "pytest",
        "metadata": {"suite": "worker"},
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


def _register_agent(
    client: TestClient,
    *,
    role_name: str,
    capabilities: list[str],
    supported_task_types: list[str],
    declare_schema: bool = True,
) -> dict:
    input_requirements = {"properties": {"text": {"type": "string"}}}
    output_contract = {"type": "object"}

    if not declare_schema:
        input_requirements = {}
        output_contract = {}

    payload = {
        "role_name": role_name,
        "description": "worker role",
        "capabilities": capabilities,
        "capability_declaration": {
            "supported_task_types": supported_task_types,
            "input_requirements": input_requirements,
            "output_contract": output_contract,
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


_cleanup_database()

from src.apps.api.app import app  # noqa: E402
from src.apps.worker.executor import run_next_task  # noqa: E402
from src.apps.worker.registry import AgentRegistry, build_default_registry  # noqa: E402
from src.packages.core.db.models import EventLogORM, ExecutionRunORM, TaskORM  # noqa: E402


client = TestClient(app)


class FailingAgent:
    def run(self, task: TaskORM, context) -> dict:
        raise RuntimeError(f"intentional failure for {task.id}")


def setup_function() -> None:
    _cleanup_database()


def teardown_function() -> None:
    _cleanup_database()


def test_worker_executes_queued_task_to_success() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(
        client,
        role_name="default_worker",
        capabilities=["default_worker"],
        supported_task_types=[],
        declare_schema=False,
    )

    response = client.post("/task-batches", json=_batch_payload("generate", suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]

    engine = create_engine(_database_url())
    with Session(engine) as session:
        run = run_next_task(session, build_default_registry())
        assert run is not None

    task_response = client.get(f"/tasks/{task_id}")
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "success"

    runs_response = client.get(f"/tasks/{task_id}/runs")
    assert runs_response.status_code == 200
    runs = runs_response.json()
    assert len(runs) == 1
    assert runs[0]["run_status"] == "success"
    assert runs[0]["output_snapshot"]["status"] == "ok"
    assert runs[0]["output_snapshot"]["task_id"] == task_id

    events_response = client.get(f"/tasks/{task_id}/events")
    assert events_response.status_code == 200
    statuses = [event["event_status"] for event in events_response.json()]
    assert statuses == ["queued", "running", "success"]


def test_worker_failure_preserves_context() -> None:
    suffix = uuid.uuid4().hex[:8]
    role_name = f"{TEST_PREFIX}fail-{suffix}"
    _register_agent(
        client,
        role_name=role_name,
        capabilities=[f"task:{role_name}"],
        supported_task_types=[role_name],
    )

    response = client.post("/task-batches", json=_batch_payload(role_name, suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]

    registry = AgentRegistry()
    registry.register(role_name, FailingAgent())

    engine = create_engine(_database_url())
    with Session(engine) as session:
        run = run_next_task(session, registry)
        assert run is not None

    task_response = client.get(f"/tasks/{task_id}")
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "failed"

    runs_response = client.get(f"/tasks/{task_id}/runs")
    assert runs_response.status_code == 200
    runs = runs_response.json()
    assert len(runs) == 1
    assert runs[0]["run_status"] == "failed"
    assert runs[0]["input_snapshot"] == {"text": "hello"}
    assert "intentional failure" in runs[0]["error_message"]
    assert any("execution failed" in line for line in runs[0]["logs"])


def test_get_run_returns_saved_execution_run() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(
        client,
        role_name="default_worker",
        capabilities=["default_worker"],
        supported_task_types=[],
        declare_schema=False,
    )

    response = client.post("/task-batches", json=_batch_payload("generate", suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]

    engine = create_engine(_database_url())
    with Session(engine) as session:
        run = run_next_task(session, build_default_registry())
        assert run is not None
        run_id = run.id

    run_response = client.get(f"/runs/{run_id}")
    assert run_response.status_code == 200
    payload = run_response.json()
    assert payload["id"] == run_id
    assert payload["task_id"] == task_id
    assert payload["run_status"] == "success"


def test_worker_does_not_run_task_with_unsatisfied_dependency() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(
        client,
        role_name="default_worker",
        capabilities=["default_worker"],
        supported_task_types=[],
        declare_schema=False,
    )

    payload = {
        "title": f"{TEST_PREFIX}dep-batch-{suffix}",
        "description": "dependency batch",
        "created_by": "pytest",
        "metadata": {"suite": "worker-dependency"},
        "tasks": [
            {
                "client_task_id": "task_1",
                "title": f"{TEST_PREFIX}dep-{suffix}-1",
                "task_type": "generate",
                "priority": "medium",
                "input_payload": {"text": "a"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
            {
                "client_task_id": "task_2",
                "title": f"{TEST_PREFIX}dep-{suffix}-2",
                "task_type": "generate",
                "priority": "medium",
                "input_payload": {"text": "b"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": ["task_1"],
            },
            {
                "client_task_id": "task_3",
                "title": f"{TEST_PREFIX}dep-{suffix}-3",
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
    tasks = response.json()["tasks"]
    task_ids = [task["task_id"] for task in tasks]

    engine = create_engine(_database_url())
    with engine.begin() as conn:
        second_task_id = tasks[1]["task_id"]
        conn.execute(
            text("UPDATE tasks SET status = 'queued' WHERE id = :task_id"),
            {"task_id": second_task_id},
        )

    with Session(engine) as session:
        first_run = run_next_task(session, build_default_registry())
        second_run = run_next_task(session, build_default_registry())
        assert first_run is not None
        assert second_run is not None

    with Session(engine) as session:
        persisted_tasks = session.query(TaskORM).filter(TaskORM.id.in_(task_ids)).all()
        status_by_id = {task.id: task.status for task in persisted_tasks}
        assert status_by_id[task_ids[0]] == "success"
        assert status_by_id[task_ids[1]] == "queued"
        assert status_by_id[task_ids[2]] == "success"

        run_task_ids = session.query(ExecutionRunORM.task_id).all()
        assert sorted(task_id for (task_id,) in run_task_ids) == sorted([task_ids[0], task_ids[2]])
