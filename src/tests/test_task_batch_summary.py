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


class SummaryDeliverableAgent:
    def run(self, task: TaskORM, context) -> dict:
        return {
            "status": "ok",
            "summary": "created deliverables",
            "result": {
                "deliverables": [
                    {
                        "type": "code_file",
                        "path": "src/summary_generated.py",
                        "language": "python",
                        "change_type": "created",
                        "content": "VALUE = 1\n",
                    },
                    {
                        "type": "test_report",
                        "command": "pytest src/tests/test_summary_generated.py",
                        "status": "passed",
                        "output": "1 passed",
                    },
                ]
            },
            "warnings": [],
            "next_action_hint": "review deliverables",
        }


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
    assert all(task["error_category"] == "routing_error" for task in payload["tasks"])
    assert payload["failure_categories"][0]["category"] == "routing_error"
    assert payload["failure_categories"][0]["count"] == 3


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
    assert task_summary["error_category"] is None
    assert len(payload["artifacts"]) == 1
    artifact = payload["artifacts"][0]
    assert artifact["raw_content"] == {}
    assert artifact["summary"] == {}
    assert artifact["structured_output"] == {}
    assert artifact["schema_version"] == "artifact.v1"


def test_summary_returns_typed_deliverable_artifacts() -> None:
    suffix = uuid.uuid4().hex[:8]
    role_name = f"{TEST_PREFIX}deliverable-{suffix}"
    _register_agent(client, role_name=role_name, supported_task_types=[role_name])
    response = client.post("/task-batches", json=_batch_payload(role_name, suffix))
    assert response.status_code == 201
    batch_id = response.json()["batch_id"]
    task_id = response.json()["tasks"][0]["task_id"]

    engine = create_engine(_database_url())
    registry = AgentRegistry()
    registry.register(role_name, SummaryDeliverableAgent())
    with Session(engine) as session:
        run = run_next_task(session, registry)
        assert run is not None

    summary_response = client.get(f"/task-batches/{batch_id}/summary")
    assert summary_response.status_code == 200
    payload = summary_response.json()
    task_summary = next(item for item in payload["tasks"] if item["task_id"] == task_id)
    assert task_summary["artifact_count"] == 3

    typed_artifacts = [
        artifact
        for artifact in payload["artifacts"]
        if artifact["task_id"] == task_id and artifact["artifact_type"] in {"code_file", "test_report"}
    ]
    assert {artifact["artifact_type"] for artifact in typed_artifacts} == {"code_file", "test_report"}
    code_file = next(artifact for artifact in typed_artifacts if artifact["artifact_type"] == "code_file")
    assert code_file["uri"] == "workspace://src/summary_generated.py"
    assert code_file["raw_content"]["content"] == "VALUE = 1\n"


def test_summary_groups_failure_categories_from_latest_task_context() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(client, role_name="default_worker", supported_task_types=[])
    response = client.post("/task-batches", json=_batch_payload("unmatched_type", suffix))
    assert response.status_code == 201
    batch_id = response.json()["batch_id"]
    task_ids = [task["task_id"] for task in response.json()["tasks"]]

    engine = create_engine(_database_url())
    with Session(engine) as session:
        tasks = [session.get(TaskORM, task_id) for task_id in task_ids]
        assert all(task is not None for task in tasks)
        first_task, second_task, third_task = tasks
        first_assignment = session.query(AssignmentORM).filter(AssignmentORM.task_id == first_task.id).first()
        third_assignment = session.query(AssignmentORM).filter(AssignmentORM.task_id == third_task.id).first()
        assert first_assignment is not None
        assert third_assignment is not None

        first_task.status = "failed"
        second_task.status = "blocked"
        third_task.status = "failed"

        session.add(
            ExecutionRunORM(
                task_id=first_task.id,
                agent_role_id=first_assignment.agent_role_id,
                run_status="failed",
                error_message="input_payload.text must be a non-empty string",
                logs=["validation failed"],
                output_snapshot={},
            )
        )
        session.add(
            ExecutionRunORM(
                task_id=third_task.id,
                agent_role_id=third_assignment.agent_role_id,
                run_status="failed",
                error_message="command failed with exit code 1",
                logs=["subprocess call failed"],
                output_snapshot={},
            )
        )
        session.commit()

    summary_response = client.get(f"/task-batches/{batch_id}/summary")
    assert summary_response.status_code == 200
    payload = summary_response.json()

    validation_task = next(item for item in payload["tasks"] if item["task_id"] == task_ids[0])
    blocked_task = next(item for item in payload["tasks"] if item["task_id"] == task_ids[1])
    tool_task = next(item for item in payload["tasks"] if item["task_id"] == task_ids[2])
    assert validation_task["error_category"] == "validation_error"
    assert blocked_task["error_category"] == "dependency_blocked"
    assert tool_task["error_category"] == "external_tool_error"

    category_counts = {item["category"]: item["count"] for item in payload["failure_categories"]}
    assert category_counts["validation_error"] == 1
    assert category_counts["dependency_blocked"] == 1
    assert category_counts["external_tool_error"] == 1
