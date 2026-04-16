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
TEST_PREFIX = "replay-test-"

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


def _register_agent(
    client: TestClient,
    *,
    role_name: str,
    supported_task_types: list[str],
    prompt_budget_policy: dict | None = None,
) -> dict:
    payload = {
        "role_name": role_name,
        "description": "replay role",
        "capabilities": [f"task:{role_name}"],
        "capability_declaration": {
            "supported_task_types": supported_task_types,
            "input_requirements": {"properties": {"text": {"type": "string"}}},
            "output_contract": {"type": "object"},
            "supports_concurrency": True,
            "allows_auto_retry": False,
        },
        "prompt_budget_policy": prompt_budget_policy or {
            "template_name": "default",
            "model_context_limit": 128000,
            "max_global_background_tokens": 256,
            "max_task_input_tokens": 4096,
            "max_dependency_summary_tokens": 1024,
            "max_result_summary_tokens": 512,
            "max_validation_rule_tokens": 512,
            "max_history_background_tokens": 256,
            "reserved_output_tokens": 256,
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
        "description": "replay batch",
        "created_by": "pytest",
        "metadata": {"suite": "replay"},
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


def _review_only_batch_payload(suffix: str) -> dict:
    return {
        "title": f"{TEST_PREFIX}batch-{suffix}",
        "description": "replay review batch",
        "created_by": "pytest",
        "metadata": {"suite": "replay"},
        "tasks": [
            {
                "client_task_id": "task_1",
                "title": f"{TEST_PREFIX}task-{suffix}-1",
                "task_type": "no_match",
                "priority": "medium",
                "input_payload": {"blob": "hello"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
            {
                "client_task_id": "task_2",
                "title": f"{TEST_PREFIX}task-{suffix}-2",
                "task_type": "no_match",
                "priority": "medium",
                "input_payload": {"blob": "world"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": ["task_1"],
            },
            {
                "client_task_id": "task_3",
                "title": f"{TEST_PREFIX}task-{suffix}-3",
                "task_type": "no_match",
                "priority": "medium",
                "input_payload": {"blob": "!"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
        ],
    }


_cleanup_database()

from src.apps.api.app import app  # noqa: E402
from src.apps.worker.executor import claim_next_task, mark_run_success  # noqa: E402
from src.packages.core.db.models import AssignmentORM, EventLogORM, ExecutionRunORM, TaskORM  # noqa: E402


client = TestClient(app)


def setup_function() -> None:
    _cleanup_database()


def teardown_function() -> None:
    _cleanup_database()


def test_run_replay_returns_input_output_logs_status_history_and_routing_snapshot() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(client, role_name=f"{TEST_PREFIX}worker-{suffix}", supported_task_types=["generate"])
    response = client.post("/task-batches", json=_batch_payload("generate", suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]

    engine = create_engine(_database_url())
    with Session(engine) as session:
        claimed = claim_next_task(session)
        assert claimed is not None
        task, run, _agent_role = claimed
        assert task.id == task_id
        completed_run = mark_run_success(session, task.id, run.id, {"artifact": "report"}, 321)
        session.commit()
        run_id = completed_run.id

    replay_response = client.get(f"/runs/{run_id}/replay")
    assert replay_response.status_code == 200
    payload = replay_response.json()
    assert payload["replay_ready"] is True
    assert payload["run"]["id"] == run_id
    assert payload["run"]["output_snapshot"]["artifact"] == "report"
    assert payload["run"]["output_snapshot"]["result_summary"]["structured_result"]["kind"] == "object"
    assert payload["run"]["budget_report"]["estimated_input_tokens"] > 0
    assert payload["routing_snapshot"]["run_id"] == run_id
    assert payload["routing_snapshot"]["task_id"] == task_id
    assert payload["routing_snapshot"]["routing_reason"] is not None
    assert payload["routing_snapshot"]["input_snapshot"]["text"] == "hello"
    assert payload["routing_snapshot"]["input_snapshot"]["task_summary"]["structured_result"]["kind"] == "object"
    assert payload["timeline"]["task_id"] == task_id
    assert any(item["new_status"] == "running" for item in payload["status_history"])
    snapshot_event = next(event for event in payload["events"] if event["event_type"] == "execution_run_replay_snapshot")
    assert snapshot_event["payload"]["budget_report"] == payload["run"]["budget_report"]


def test_run_replay_uses_snapshot_even_if_assignment_changes_later() -> None:
    suffix = uuid.uuid4().hex[:8]
    first_role = _register_agent(client, role_name=f"{TEST_PREFIX}first-{suffix}", supported_task_types=["generate"])
    second_role = _register_agent(client, role_name=f"{TEST_PREFIX}second-{suffix}", supported_task_types=["other"])
    response = client.post("/task-batches", json=_batch_payload("generate", suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]

    engine = create_engine(_database_url())
    with Session(engine) as session:
        claimed = claim_next_task(session)
        assert claimed is not None
        task, run, _agent_role = claimed
        mark_run_success(session, task.id, run.id, {"artifact": "report"}, 111)
        later_assignment = AssignmentORM(
            task_id=task.id,
            agent_role_id=second_role["id"],
            routing_reason="later reassignment",
            assignment_status="active",
        )
        session.add(later_assignment)
        task_row = session.get(TaskORM, task.id)
        assert task_row is not None
        task_row.assigned_agent_role = second_role["role_name"]
        session.commit()
        run_id = run.id

    replay_response = client.get(f"/runs/{run_id}/replay")
    assert replay_response.status_code == 200
    payload = replay_response.json()
    assert payload["routing_snapshot"]["agent_role_name"] == first_role["role_name"]
    assert payload["task"]["assigned_agent_role"] == second_role["role_name"]


def test_batch_replay_returns_key_steps_for_mixed_tasks_including_review_only_task() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(client, role_name=f"{TEST_PREFIX}worker-{suffix}", supported_task_types=["generate"])
    routed_response = client.post("/task-batches", json=_batch_payload("generate", suffix))
    assert routed_response.status_code == 201
    batch_id = routed_response.json()["batch_id"]

    review_response = client.post("/task-batches", json=_review_only_batch_payload(f"{suffix}-review"))
    assert review_response.status_code == 201
    review_task_id = review_response.json()["tasks"][0]["task_id"]

    engine = create_engine(_database_url())
    with Session(engine) as session:
        claimed = claim_next_task(session)
        assert claimed is not None
        task, run, _agent_role = claimed
        mark_run_success(session, task.id, run.id, {"artifact": "batch-report"}, 222)
        session.commit()

    batch_replay = client.get(f"/task-batches/{batch_id}/replay")
    assert batch_replay.status_code == 200
    payload = batch_replay.json()
    assert payload["batch"]["id"] == batch_id
    assert payload["derived_status"] in {"pending", "partially_failed", "success", "running"}
    assert len(payload["items"]) == 3
    assert any(item["latest_run"] is not None for item in payload["items"])
    assert all("timeline" in item for item in payload["items"])

    review_batch_id = review_response.json()["batch_id"]
    review_batch_replay = client.get(f"/task-batches/{review_batch_id}/replay")
    assert review_batch_replay.status_code == 200
    review_items = review_batch_replay.json()["items"]
    review_item = next(item for item in review_items if item["task_id"] == review_task_id)
    assert review_item["latest_run"] is None
    assert review_item["routing_snapshot"] is None
    assert any(timeline_item["stage"] == "review" for timeline_item in review_item["timeline"]["items"])


def test_replay_endpoints_return_404_for_unknown_resources() -> None:
    assert client.get("/runs/not_found/replay").status_code == 404
    assert client.get("/task-batches/not_found/replay").status_code == 404


def test_run_replay_shows_trimmed_input_snapshot_when_context_was_reduced() -> None:
    suffix = uuid.uuid4().hex[:8]
    task_type = f"{TEST_PREFIX}trim-task-{suffix}"
    _register_agent(
        client,
        role_name=f"{TEST_PREFIX}trim-{suffix}",
        supported_task_types=[task_type],
        prompt_budget_policy={
            "template_name": "trim-test",
            "model_context_limit": 1024,
            "max_global_background_tokens": 256,
            "max_task_input_tokens": 200000,
            "max_dependency_summary_tokens": 256,
            "max_result_summary_tokens": 128,
            "max_validation_rule_tokens": 256,
            "max_history_background_tokens": 64,
            "reserved_output_tokens": 128,
        },
    )
    payload = _batch_payload(task_type, suffix)
    payload["tasks"][0]["input_payload"] = {"text": "x" * 600000}
    response = client.post("/task-batches", json=payload)
    assert response.status_code == 201

    engine = create_engine(_database_url())
    with Session(engine) as session:
        claimed = claim_next_task(session)
        assert claimed is not None
        task, run, _agent_role = claimed
        completed_run = mark_run_success(session, task.id, run.id, {"artifact": "trimmed"}, 100)
        session.commit()
        run_id = completed_run.id

    replay_response = client.get(f"/runs/{run_id}/replay")
    assert replay_response.status_code == 200
    replay = replay_response.json()
    assert replay["run"]["budget_report"]["trim_applied"] is True
    assert replay["routing_snapshot"]["input_snapshot"] == replay["run"]["input_snapshot"]
    assert replay["run"]["input_snapshot"] != {"text": "x" * 600000}


def test_run_replay_shows_downstream_summary_instead_of_full_dependency_output() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(client, role_name=f"{TEST_PREFIX}worker-{suffix}", supported_task_types=["generate"])
    response = client.post("/task-batches", json=_batch_payload("generate", suffix))
    assert response.status_code == 201
    dependent_task_id = response.json()["tasks"][1]["task_id"]

    engine = create_engine(_database_url())
    with Session(engine) as session:
        first_claim = claim_next_task(session)
        assert first_claim is not None
        upstream_task, upstream_run, _agent_role = first_claim
        mark_run_success(session, upstream_task.id, upstream_run.id, {"artifact": "x" * 4000}, 100)
        session.commit()

    with Session(engine) as session:
        second_claim = claim_next_task(session)
        assert second_claim is not None
        task, run, _agent_role = second_claim
        assert task.id == dependent_task_id
        session.commit()
        run_id = run.id

    replay_response = client.get(f"/runs/{run_id}/replay")
    assert replay_response.status_code == 200
    replay = replay_response.json()
    downstream_summary = replay["routing_snapshot"]["input_snapshot"]["downstream_summary"]
    assert len(downstream_summary) == 1
    assert downstream_summary[0]["result_summary"]["summary"]["artifact"].endswith("[summary len=4000]")
    assert "latest_output" not in downstream_summary[0]
