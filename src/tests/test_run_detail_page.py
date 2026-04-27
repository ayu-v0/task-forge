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
TEST_PREFIX = "run-detail-test-"

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
        "description": "run detail role",
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
        roles_response = client.get("/agents")
        assert roles_response.status_code == 200
        return next(role for role in roles_response.json() if role["role_name"] == role_name)
    return response.json()


def _batch_payload(task_type: str, suffix: str) -> dict:
    return {
        "title": f"{TEST_PREFIX}batch-{suffix}",
        "description": "run detail batch",
        "created_by": "pytest",
        "metadata": {"suite": "run-detail"},
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


client = TestClient(app)


def setup_function() -> None:
    _cleanup_database()


def teardown_function() -> None:
    _cleanup_database()


def test_run_detail_endpoint_returns_routing_and_retry_history() -> None:
    suffix = uuid.uuid4().hex[:8]
    role_name = f"{TEST_PREFIX}worker-{suffix}"
    _register_agent(client, role_name=role_name, supported_task_types=["generate"])
    response = client.post("/task-batches", json=_batch_payload("generate", suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]

    engine = create_engine(_database_url())
    with Session(engine) as session:
        task = session.get(TaskORM, task_id)
        assert task is not None
        assignment = session.query(AssignmentORM).filter(AssignmentORM.task_id == task.id).first()
        assert assignment is not None
        base_time = datetime.now(timezone.utc)
        first_run = ExecutionRunORM(
            task_id=task.id,
            agent_role_id=assignment.agent_role_id,
            run_status="failed",
            started_at=base_time,
            finished_at=base_time + timedelta(seconds=1),
            input_snapshot={"attempt": 1},
            output_snapshot={},
            logs=["compile started", "compile failed"],
            error_message="first failure",
            token_usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            budget_report={
                "model_context_limit": 128000,
                "system_prompt_tokens": 40,
                "task_input_tokens": 10,
                "dependency_summary_tokens": 0,
                "estimated_input_tokens": 50,
                "reserved_output_tokens": 256,
                "safe_budget": 127694,
                "overflow_risk": False,
            },
            latency_ms=1000,
        )
        second_run = ExecutionRunORM(
            task_id=task.id,
            agent_role_id=assignment.agent_role_id,
            run_status="success",
            started_at=base_time + timedelta(seconds=2),
            finished_at=base_time + timedelta(seconds=3),
            input_snapshot={"attempt": 2},
            output_snapshot={"artifact": "report"},
            logs=["compile started", "compile succeeded"],
            token_usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            budget_report={
                "model_context_limit": 128000,
                "system_prompt_tokens": 41,
                "task_input_tokens": 11,
                "dependency_summary_tokens": 4,
                "estimated_input_tokens": 56,
                "reserved_output_tokens": 256,
                "safe_budget": 127688,
                "overflow_risk": False,
            },
            latency_ms=900,
        )
        session.add(first_run)
        session.flush()
        session.add(second_run)
        session.flush()
        session.add(
            EventLogORM(
                batch_id=task.batch_id,
                task_id=task.id,
                run_id=second_run.id,
                event_type="run_completed",
                event_status="success",
                message="completed",
                payload={"run_id": second_run.id},
            )
        )
        session.commit()
        run_id = second_run.id

    detail_response = client.get(f"/runs/{run_id}/detail")
    assert detail_response.status_code == 200
    payload = detail_response.json()
    assert payload["run"]["id"] == run_id
    assert payload["task"]["task_id"] == task_id
    assert payload["routing"]["agent_role_name"] == role_name
    assert payload["routing"]["routing_reason"] == f"capability-ranked route selected role={role_name} via task_type,schema (no_history)"
    assert [item["run_status"] for item in payload["retry_history"]] == ["success", "failed"]
    assert payload["retry_history"][0]["is_current"] is True
    assert payload["run"]["token_usage"]["total_tokens"] == 8
    assert payload["run"]["budget_report"]["estimated_input_tokens"] == 56
    assert payload["run"]["budget_report"]["overflow_risk"] is False
    assert payload["cost_estimate"] == 0.000011
    assert payload["error_category"] is None
    assert payload["result_summary"]["status"] == "success"
    assert payload["run"]["result_summary"] == payload["result_summary"]
    assert payload["events"][-1]["event_type"] == "run_completed"


def test_run_detail_endpoint_returns_error_category_for_failed_run() -> None:
    suffix = uuid.uuid4().hex[:8]
    role_name = f"{TEST_PREFIX}timeout-{suffix}"
    _register_agent(client, role_name=role_name, supported_task_types=["generate"])
    response = client.post("/task-batches", json=_batch_payload("generate", suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]

    engine = create_engine(_database_url())
    with Session(engine) as session:
        task = session.get(TaskORM, task_id)
        assert task is not None
        assignment = session.query(AssignmentORM).filter(AssignmentORM.task_id == task.id).first()
        assert assignment is not None
        run = ExecutionRunORM(
            task_id=task.id,
            agent_role_id=assignment.agent_role_id,
            run_status="failed",
            error_message="tool timed out while waiting for response",
            logs=["command timed out"],
            input_snapshot={"attempt": 1},
            output_snapshot={},
        )
        session.add(run)
        session.commit()
        run_id = run.id

    detail_response = client.get(f"/runs/{run_id}/detail")
    assert detail_response.status_code == 200
    payload = detail_response.json()
    assert payload["run"]["run_status"] == "failed"
    assert payload["error_category"] == "timeout"
    assert payload["result_summary"]["status"] == "error"


def test_run_detail_endpoint_handles_cancelled_run_without_logs() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(client, role_name="default_worker", supported_task_types=[])
    response = client.post("/task-batches", json=_batch_payload("unmatched_type", suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]

    engine = create_engine(_database_url())
    with Session(engine) as session:
        task = session.get(TaskORM, task_id)
        assert task is not None
        assignment = session.query(AssignmentORM).filter(AssignmentORM.task_id == task.id).first()
        assert assignment is not None
        run = ExecutionRunORM(
            task_id=task.id,
            agent_role_id=assignment.agent_role_id,
            run_status="cancelled",
            cancel_reason="user requested cancellation",
            input_snapshot={"attempt": 1},
            output_snapshot={},
            logs=[],
            latency_ms=None,
        )
        session.add(run)
        session.commit()
        run_id = run.id

    detail_response = client.get(f"/runs/{run_id}/detail")
    assert detail_response.status_code == 200
    payload = detail_response.json()
    assert payload["run"]["run_status"] == "cancelled"
    assert payload["run"]["cancel_reason"] == "user requested cancellation"
    assert payload["run"]["logs"] == []
    assert payload["error_category"] is None
    assert payload["result_summary"]["status"] == "empty"


def test_console_run_detail_page_is_accessible() -> None:
    response = client.get("/console/runs/sample-run-id")
    assert response.status_code == 200
    assert "Run Detail" in response.text
    assert "/console/assets/run-detail.js" in response.text


def test_run_detail_page_assets_include_task_lifecycle_timeline() -> None:
    page_response = client.get("/console/runs/sample-run-id")
    assert page_response.status_code == 200
    assert "Lifecycle timeline" in page_response.text

    asset_response = client.get("/console/assets/run-detail.js")
    assert asset_response.status_code == 200
    assert "/tasks/${detail.task.task_id}/timeline" in asset_response.text
    assert "Cost estimate" in asset_response.text
    assert "Error category" in asset_response.text
    assert "Overflow risk" in asset_response.text


def test_batch_detail_assets_link_to_run_detail_page() -> None:
    response = client.get("/console/assets/batch-detail.js")
    assert response.status_code == 200
    assert "/console/runs/" in response.text
