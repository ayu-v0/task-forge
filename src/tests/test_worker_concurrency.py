from __future__ import annotations

import os
import sys
import threading
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[2]
TEST_PREFIX = "worker-concurrency-"

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
            text("DELETE FROM agent_roles WHERE role_name = 'default_worker'"),
        )


_cleanup_database()

from src.apps.api.app import app  # noqa: E402
from src.apps.worker.executor import run_next_task  # noqa: E402
from src.apps.worker.registry import AgentRegistry  # noqa: E402
from src.packages.core.db.models import ExecutionRunORM, TaskORM  # noqa: E402


client = TestClient(app)


class SlowAgent:
    def run(self, task: TaskORM, context) -> dict:
        import time

        time.sleep(0.2)
        return {"status": "ok", "task_id": task.id}


def _register_default_worker() -> None:
    payload = {
        "role_name": "default_worker",
        "description": "worker role",
        "capabilities": ["default_worker"],
        "capability_declaration": {
            "supported_task_types": [],
            "input_requirements": {},
            "output_contract": {},
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


def setup_function() -> None:
    _cleanup_database()


def teardown_function() -> None:
    _cleanup_database()


def test_concurrent_workers_do_not_consume_same_task_twice() -> None:
    role_name = "default_worker"
    _register_default_worker()
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "title": f"{TEST_PREFIX}batch-{suffix}",
        "description": "concurrency batch",
        "created_by": "pytest",
        "metadata": {"suite": "worker-concurrency"},
        "tasks": [
            {
                "client_task_id": "task_1",
                "title": f"{TEST_PREFIX}task-{suffix}-1",
                "task_type": "unmatched_type",
                "priority": "medium",
                "input_payload": {"text": "hello"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
            {
                "client_task_id": "task_2",
                "title": f"{TEST_PREFIX}task-{suffix}-2",
                "task_type": "unmatched_type",
                "priority": "medium",
                "input_payload": {"text": "world"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": ["task_1"],
            },
            {
                "client_task_id": "task_3",
                "title": f"{TEST_PREFIX}task-{suffix}-3",
                "task_type": "unmatched_type",
                "priority": "medium",
                "input_payload": {"text": "!"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": ["task_1"],
            },
        ],
    }
    response = client.post("/task-batches", json=payload)
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]

    engine = create_engine(_database_url())
    barrier = threading.Barrier(2)
    results: list[str | None] = [None, None]
    registry = AgentRegistry()
    registry.register(role_name, SlowAgent())

    def _run_once(index: int) -> None:
        with Session(engine) as session:
            barrier.wait()
            run = run_next_task(session, registry)
            results[index] = None if run is None else run.task_id

    threads = [threading.Thread(target=_run_once, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    claimed = [result for result in results if result is not None]
    assert claimed == [task_id]

    with Session(engine) as session:
        runs = session.query(ExecutionRunORM).filter(ExecutionRunORM.task_id == task_id).all()
        assert len(runs) == 1
