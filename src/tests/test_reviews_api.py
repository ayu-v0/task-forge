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
TEST_PREFIX = "review-test-"

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
        "description": "review role",
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


def _batch_payload(suffix: str) -> dict:
    return {
        "title": f"{TEST_PREFIX}batch-{suffix}",
        "description": "review batch",
        "created_by": "pytest",
        "metadata": {"suite": "review"},
        "tasks": [
            {
                "client_task_id": "task_1",
                "title": f"{TEST_PREFIX}task-{suffix}-1",
                "task_type": "no_match",
                "priority": "medium",
                "input_payload": {"text": "hello"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
            {
                "client_task_id": "task_2",
                "title": f"{TEST_PREFIX}task-{suffix}-2",
                "task_type": "no_match",
                "priority": "medium",
                "input_payload": {"text": "world"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": ["task_1"],
            },
            {
                "client_task_id": "task_3",
                "title": f"{TEST_PREFIX}task-{suffix}-3",
                "task_type": "no_match",
                "priority": "medium",
                "input_payload": {"text": "!"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
        ],
    }


def _review_id_for_task(task_id: str) -> str:
    return client.get(f"/tasks/{task_id}/reviews").json()[0]["id"]


def _set_review_policy(review_id: str, *, timeout_policy: str, deadline_at: datetime, reason_category: str = "routing_failure") -> None:
    engine = create_engine(_database_url())
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE review_checkpoints
                SET timeout_policy = :timeout_policy,
                    deadline_at = :deadline_at,
                    reason_category = :reason_category
                WHERE id = :review_id
                """
            ),
            {
                "timeout_policy": timeout_policy,
                "deadline_at": deadline_at,
                "reason_category": reason_category,
                "review_id": review_id,
            },
        )


_cleanup_database()

from src.apps.api.app import app  # noqa: E402
from src.apps.worker.executor import run_next_task  # noqa: E402
from src.apps.worker.registry import AgentRegistry  # noqa: E402
from src.packages.core.db.models import AssignmentORM, ExecutionRunORM, EventLogORM, ReviewCheckpointORM, TaskORM  # noqa: E402


client = TestClient(app)


class ReviewAgent:
    def run(self, task: TaskORM, context) -> dict:
        return {"status": "ok", "task_id": task.id, "run_id": context.run_id}


def setup_function() -> None:
    _cleanup_database()


def teardown_function() -> None:
    _cleanup_database()


def test_lists_task_reviews_and_exposes_reason_category_timeout_policy_and_deadline() -> None:
    suffix = uuid.uuid4().hex[:8]
    response = client.post("/task-batches", json=_batch_payload(suffix))
    assert response.status_code == 201

    task_id = response.json()["tasks"][0]["task_id"]
    reviews_response = client.get(f"/tasks/{task_id}/reviews")
    assert reviews_response.status_code == 200
    reviews = reviews_response.json()
    assert len(reviews) == 1
    assert reviews[0]["review_status"] == "pending"
    assert reviews[0]["reason_category"] == "routing_failure"
    assert reviews[0]["timeout_policy"] == "fail_closed"
    assert reviews[0]["deadline_at"] is not None
    assert "No eligible agent role found" in reviews[0]["reason"]

    review_id = reviews[0]["id"]
    detail_response = client.get(f"/reviews/{review_id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["id"] == review_id

    events_response = client.get(f"/tasks/{task_id}/events")
    assert events_response.status_code == 200
    event_types = [event["event_type"] for event in events_response.json()]
    assert "review_checkpoint_created" in event_types


def test_approve_review_assigns_role_and_worker_executes_task() -> None:
    suffix = uuid.uuid4().hex[:8]
    response = client.post("/task-batches", json=_batch_payload(suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]
    review_id = _review_id_for_task(task_id)

    role_name = f"{TEST_PREFIX}worker-{suffix}"
    role = _register_agent(client, role_name=role_name, supported_task_types=[role_name])

    approve_response = client.post(
        f"/reviews/{review_id}/approve",
        json={
            "reviewer": "alice",
            "review_comment": "manual approval",
            "agent_role_id": role["id"],
        },
    )
    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "queued"
    assert approve_response.json()["assigned_agent_role"] == role_name

    registry = AgentRegistry()
    registry.register(role_name, ReviewAgent())
    engine = create_engine(_database_url())
    with Session(engine) as session:
        run = run_next_task(session, registry)
        assert run is not None
        assert run.task_id == task_id

    task_response = client.get(f"/tasks/{task_id}")
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "success"

    events_response = client.get(f"/tasks/{task_id}/events")
    event_types = [event["event_type"] for event in events_response.json()]
    assert "review_approved" in event_types
    assert "task_review_resolved" in event_types


def test_reassign_review_supersedes_previous_assignment_and_keeps_task_runnable() -> None:
    suffix = uuid.uuid4().hex[:8]
    response = client.post("/task-batches", json=_batch_payload(suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]
    review_id = _review_id_for_task(task_id)

    first_role = _register_agent(client, role_name=f"{TEST_PREFIX}first-{suffix}", supported_task_types=["dummy"])
    second_role = _register_agent(client, role_name=f"{TEST_PREFIX}second-{suffix}", supported_task_types=["dummy"])

    engine = create_engine(_database_url())
    with Session(engine) as session:
        session.add(
            AssignmentORM(
                task_id=task_id,
                agent_role_id=first_role["id"],
                routing_reason="stale assignment",
                assignment_status="active",
            )
        )
        task = session.get(TaskORM, task_id)
        assert task is not None
        task.assigned_agent_role = first_role["role_name"]
        session.commit()

    reassign_response = client.post(
        f"/reviews/{review_id}/reassign",
        json={
            "reviewer": "bob",
            "review_comment": "assign to fallback role",
            "agent_role_id": second_role["id"],
        },
    )
    assert reassign_response.status_code == 200
    assert reassign_response.json()["status"] == "queued"
    assert reassign_response.json()["assigned_agent_role"] == second_role["role_name"]

    with Session(engine) as session:
        assignments = session.query(AssignmentORM).filter(AssignmentORM.task_id == task_id).order_by(AssignmentORM.assigned_at.asc()).all()
        assert len(assignments) == 2
        assert assignments[0].assignment_status == "superseded"
        assert assignments[1].assignment_status == "active"

    events = client.get(f"/tasks/{task_id}/events").json()
    assert "review_reassigned" in [event["event_type"] for event in events]


def test_bulk_approve_reviews_returns_per_item_results() -> None:
    suffix = uuid.uuid4().hex[:8]
    response = client.post("/task-batches", json=_batch_payload(suffix))
    assert response.status_code == 201
    task_ids = [item["task_id"] for item in response.json()["tasks"]]
    review_ids = [_review_id_for_task(task_id) for task_id in task_ids]
    role = _register_agent(client, role_name=f"{TEST_PREFIX}bulk-{suffix}", supported_task_types=["dummy"])

    bulk_response = client.post(
        "/reviews/bulk/approve",
        json={
            "review_ids": [review_ids[0], "review_missing", review_ids[2]],
            "reviewer": "alice",
            "review_comment": "bulk approval",
            "agent_role_id": role["id"],
        },
    )
    assert bulk_response.status_code == 200
    items = bulk_response.json()["items"]
    assert len(items) == 3
    assert items[0]["ok"] is True
    assert items[0]["status"] == "queued"
    assert items[1]["ok"] is False
    assert "not found" in items[1]["detail"].lower()
    assert items[2]["ok"] is True


def test_bulk_reject_handles_already_resolved_review_without_rolling_back_others() -> None:
    suffix = uuid.uuid4().hex[:8]
    response = client.post("/task-batches", json=_batch_payload(suffix))
    assert response.status_code == 201
    task_ids = [item["task_id"] for item in response.json()["tasks"]]
    review_ids = [_review_id_for_task(task_id) for task_id in task_ids]
    role = _register_agent(client, role_name=f"{TEST_PREFIX}once-{suffix}", supported_task_types=["dummy"])

    first_approve = client.post(
        f"/reviews/{review_ids[0]}/approve",
        json={"reviewer": "alice", "review_comment": "approve one", "agent_role_id": role["id"]},
    )
    assert first_approve.status_code == 200

    bulk_reject = client.post(
        "/reviews/bulk/reject",
        json={
            "review_ids": review_ids,
            "reviewer": "alice",
            "review_comment": "bulk reject",
        },
    )
    assert bulk_reject.status_code == 200
    items = bulk_reject.json()["items"]
    assert len(items) == 3
    assert items[0]["ok"] is False
    assert "cannot be decided" in items[0]["detail"]
    assert items[1]["ok"] is True
    assert items[1]["status"] == "failed"
    assert items[2]["ok"] is True


def test_process_timeouts_fail_closed_marks_task_failed() -> None:
    suffix = uuid.uuid4().hex[:8]
    response = client.post("/task-batches", json=_batch_payload(suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]
    review_id = _review_id_for_task(task_id)
    _set_review_policy(
        review_id,
        timeout_policy="fail_closed",
        deadline_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )

    timeout_response = client.post("/reviews/process-timeouts", json={"limit": 10})
    assert timeout_response.status_code == 200
    assert timeout_response.json()["processed_count"] == 1

    task_response = client.get(f"/tasks/{task_id}")
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "failed"


def test_process_timeouts_cancel_task_marks_task_cancelled() -> None:
    suffix = uuid.uuid4().hex[:8]
    response = client.post("/task-batches", json=_batch_payload(suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]
    review_id = _review_id_for_task(task_id)
    _set_review_policy(
        review_id,
        timeout_policy="cancel_task",
        deadline_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )

    timeout_response = client.post("/reviews/process-timeouts", json={"limit": 10})
    assert timeout_response.status_code == 200
    assert timeout_response.json()["processed_count"] == 1

    task_response = client.get(f"/tasks/{task_id}")
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "cancelled"


def test_process_timeouts_escalate_creates_new_pending_review_and_is_idempotent() -> None:
    suffix = uuid.uuid4().hex[:8]
    response = client.post("/task-batches", json=_batch_payload(suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]
    review_id = _review_id_for_task(task_id)
    _set_review_policy(
        review_id,
        timeout_policy="escalate",
        deadline_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )

    first_timeout = client.post("/reviews/process-timeouts", json={"limit": 10})
    assert first_timeout.status_code == 200
    assert first_timeout.json()["processed_count"] == 1

    reviews = client.get(f"/tasks/{task_id}/reviews").json()
    assert len(reviews) == 2
    assert reviews[0]["review_status"] == "rejected"
    assert reviews[1]["review_status"] == "pending"
    assert reviews[1]["reason_category"] == "manual_override"

    second_timeout = client.post("/reviews/process-timeouts", json={"limit": 10})
    assert second_timeout.status_code == 200
    assert second_timeout.json()["processed_count"] == 0
    reviews_after = client.get(f"/tasks/{task_id}/reviews").json()
    assert len(reviews_after) == 2


def test_cannot_decide_review_after_task_is_cancelled() -> None:
    suffix = uuid.uuid4().hex[:8]
    response = client.post("/task-batches", json=_batch_payload(suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]
    review_id = _review_id_for_task(task_id)

    cancel_response = client.post(f"/tasks/{task_id}/cancel", json={"reason": "user stop"})
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"

    role = _register_agent(
        client,
        role_name=f"{TEST_PREFIX}cancelled-{suffix}",
        supported_task_types=[f"{TEST_PREFIX}cancelled-{suffix}"],
    )
    approve_response = client.post(
        f"/reviews/{review_id}/approve",
        json={
            "reviewer": "alice",
            "review_comment": "too late",
            "agent_role_id": role["id"],
        },
    )
    assert approve_response.status_code == 409

    reject_response = client.post(
        f"/reviews/{review_id}/reject",
        json={
            "reviewer": "alice",
            "review_comment": "too late",
        },
    )
    assert reject_response.status_code == 409


def test_cannot_approve_review_twice() -> None:
    suffix = uuid.uuid4().hex[:8]
    response = client.post("/task-batches", json=_batch_payload(suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]
    review_id = _review_id_for_task(task_id)
    role = _register_agent(
        client,
        role_name=f"{TEST_PREFIX}repeat-{suffix}",
        supported_task_types=[f"{TEST_PREFIX}repeat-{suffix}"],
    )

    first_response = client.post(
        f"/reviews/{review_id}/approve",
        json={
            "reviewer": "alice",
            "review_comment": "approved",
            "agent_role_id": role["id"],
        },
    )
    assert first_response.status_code == 200

    second_response = client.post(
        f"/reviews/{review_id}/approve",
        json={
            "reviewer": "alice",
            "review_comment": "approved again",
            "agent_role_id": role["id"],
        },
    )
    assert second_response.status_code == 409
