from __future__ import annotations

import os
import sys
import uuid
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


_cleanup_database()

from src.apps.api.app import app  # noqa: E402
from src.apps.worker.executor import run_next_task  # noqa: E402
from src.apps.worker.registry import AgentRegistry  # noqa: E402
from src.packages.core.db.models import ExecutionRunORM, TaskORM  # noqa: E402


client = TestClient(app)


class ReviewAgent:
    def run(self, task: TaskORM, context) -> dict:
        return {"status": "ok", "task_id": task.id, "run_id": context.run_id}


def setup_function() -> None:
    _cleanup_database()


def teardown_function() -> None:
    _cleanup_database()


def test_lists_task_reviews_and_review_created_event() -> None:
    suffix = uuid.uuid4().hex[:8]
    response = client.post("/task-batches", json=_batch_payload(suffix))
    assert response.status_code == 201

    task_id = response.json()["tasks"][0]["task_id"]
    reviews_response = client.get(f"/tasks/{task_id}/reviews")
    assert reviews_response.status_code == 200
    reviews = reviews_response.json()
    assert len(reviews) == 1
    assert reviews[0]["review_status"] == "pending"
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

    reviews = client.get(f"/tasks/{task_id}/reviews").json()
    review_id = reviews[0]["id"]

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


def test_approve_review_with_unsatisfied_dependency_moves_task_to_blocked() -> None:
    suffix = uuid.uuid4().hex[:8]
    response = client.post("/task-batches", json=_batch_payload(suffix))
    assert response.status_code == 201
    dependent_task_id = response.json()["tasks"][1]["task_id"]

    review_id = client.get(f"/tasks/{dependent_task_id}/reviews").json()[0]["id"]
    role = _register_agent(
        client,
        role_name=f"{TEST_PREFIX}blocked-{suffix}",
        supported_task_types=[f"{TEST_PREFIX}blocked-{suffix}"],
    )

    approve_response = client.post(
        f"/reviews/{review_id}/approve",
        json={
            "reviewer": "bob",
            "review_comment": "approved after manual routing",
            "agent_role_id": role["id"],
        },
    )
    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "blocked"


def test_reject_review_marks_task_failed_and_prevents_execution() -> None:
    suffix = uuid.uuid4().hex[:8]
    response = client.post("/task-batches", json=_batch_payload(suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]

    review_id = client.get(f"/tasks/{task_id}/reviews").json()[0]["id"]
    reject_response = client.post(
        f"/reviews/{review_id}/reject",
        json={
            "reviewer": "alice",
            "review_comment": "insufficient confidence",
        },
    )
    assert reject_response.status_code == 200
    assert reject_response.json()["status"] == "failed"

    engine = create_engine(_database_url())
    with Session(engine) as session:
        assert run_next_task(session, AgentRegistry()) is None
        assert session.query(ExecutionRunORM).filter(ExecutionRunORM.task_id == task_id).count() == 0

    events_response = client.get(f"/tasks/{task_id}/events")
    event_types = [event["event_type"] for event in events_response.json()]
    assert "review_rejected" in event_types
    assert "task_review_resolved" in event_types


def test_cannot_decide_review_after_task_is_cancelled() -> None:
    suffix = uuid.uuid4().hex[:8]
    response = client.post("/task-batches", json=_batch_payload(suffix))
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]
    review_id = client.get(f"/tasks/{task_id}/reviews").json()[0]["id"]

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
    review_id = client.get(f"/tasks/{task_id}/reviews").json()[0]["id"]
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
