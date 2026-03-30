from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[2]
DEMO_PREFIX = "builtin-demo-"

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
            {"prefix": f"{DEMO_PREFIX}%"},
        )


def _demo_payload(suffix: str) -> dict:
    return {
        "title": f"{DEMO_PREFIX}batch-{suffix}",
        "description": "built-in three role demo",
        "created_by": "pytest",
        "metadata": {"suite": "builtin-chain"},
        "tasks": [
            {
                "client_task_id": "task_1",
                "title": f"{DEMO_PREFIX}planner-{suffix}",
                "task_type": "planner_preprocess",
                "priority": "medium",
                "input_payload": {"text": "draft implementation plan"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
            {
                "client_task_id": "task_2",
                "title": f"{DEMO_PREFIX}worker-{suffix}",
                "task_type": "worker_execute",
                "priority": "medium",
                "input_payload": {"text": "execute planned task"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": ["task_1"],
            },
            {
                "client_task_id": "task_3",
                "title": f"{DEMO_PREFIX}reviewer-{suffix}",
                "task_type": "reviewer_validate",
                "priority": "medium",
                "input_payload": {"raw_output": {"status": "ok"}},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": ["task_2"],
            },
        ],
    }


_cleanup_database()

from src.apps.api.app import app  # noqa: E402
from src.apps.worker.service import WorkerService  # noqa: E402

def setup_function() -> None:
    _cleanup_database()


def teardown_function() -> None:
    _cleanup_database()


def test_builtin_roles_seeded_once_and_demo_chain_runs() -> None:
    with TestClient(app) as client:
        engine = create_engine(_database_url())
        with engine.connect() as conn:
            planner_count = conn.execute(
                text("SELECT count(*) FROM agent_roles WHERE role_name = 'planner_agent'")
            ).scalar_one()
            worker_count = conn.execute(
                text("SELECT count(*) FROM agent_roles WHERE role_name = 'worker_agent'")
            ).scalar_one()
            reviewer_count = conn.execute(
                text("SELECT count(*) FROM agent_roles WHERE role_name = 'reviewer_agent'")
            ).scalar_one()

        assert planner_count == 1
        assert worker_count == 1
        assert reviewer_count == 1

        suffix = uuid.uuid4().hex[:8]
        create_response = client.post("/task-batches", json=_demo_payload(suffix))
        assert create_response.status_code == 201
        created = create_response.json()
        created_ids = [task["task_id"] for task in created["tasks"]]

        with Session(engine) as session:
            worker = WorkerService(session)
            first_run = worker.run_once()
            second_run = worker.run_once()
            third_run = worker.run_once()
            assert first_run is not None
            assert second_run is not None
            assert third_run is not None
            run_summaries = [
                {"run_id": first_run.id, "task_id": first_run.task_id},
                {"run_id": second_run.id, "task_id": second_run.task_id},
                {"run_id": third_run.id, "task_id": third_run.task_id},
            ]

        assert {item["task_id"] for item in run_summaries} == set(created_ids)

        reviewer_task_id = created["tasks"][2]["task_id"]
        run_body = {}
        for summary in run_summaries:
            reviewer_run_response = client.get(f"/runs/{summary['run_id']}")
            assert reviewer_run_response.status_code == 200
            candidate = reviewer_run_response.json()
            if candidate["task_id"] == reviewer_task_id:
                run_body = candidate
                break

        assert run_body["task_id"] == reviewer_task_id
        assert run_body["output_snapshot"]["stage"] == "reviewer"
        assert run_body["output_snapshot"]["validation_passed"] is True
        assert run_body["output_snapshot"]["needs_manual_review"] is False

        for task_id in created_ids:
            task_response = client.get(f"/tasks/{task_id}")
            assert task_response.status_code == 200
            assert task_response.json()["status"] == "success"

            events_response = client.get(f"/tasks/{task_id}/events")
            assert events_response.status_code == 200
            statuses = [
                event["event_status"]
                for event in events_response.json()
                if event["event_type"] == "task_status_changed"
            ]
            assert statuses == ["queued", "running", "success"]
