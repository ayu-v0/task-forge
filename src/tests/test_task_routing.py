from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text


ROOT = Path(__file__).resolve().parents[2]
ROUTING_PREFIX = "routing-test-"

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
        conn.execute(text("DELETE FROM task_batches"))
        conn.execute(text("DELETE FROM agent_roles"))


def _batch_payload(task_type: str, suffix: str) -> dict:
    return {
        "title": f"{ROUTING_PREFIX}batch-{suffix}",
        "description": "routing batch",
        "created_by": "pytest",
        "metadata": {"suite": "routing"},
        "tasks": [
            {
                "client_task_id": "task_1",
                "title": f"{ROUTING_PREFIX}task-{suffix}-1",
                "task_type": task_type,
                "priority": "medium",
                "input_payload": {"text": "hello"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
            {
                "client_task_id": "task_2",
                "title": f"{ROUTING_PREFIX}task-{suffix}-2",
                "task_type": task_type,
                "priority": "medium",
                "input_payload": {"text": "world"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
            {
                "client_task_id": "task_3",
                "title": f"{ROUTING_PREFIX}task-{suffix}-3",
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
    input_properties: dict | None = None,
    output_type: str = "object",
    declare_schema: bool = True,
) -> dict:
    input_requirements = {"properties": input_properties or {"text": {"type": "string"}}}
    output_contract = {"type": output_type}

    if not declare_schema:
        input_requirements = {}
        output_contract = {}

    payload = {
        "role_name": role_name,
        "description": "routing role",
        "capabilities": capabilities,
        "capability_declaration": {
            "supported_task_types": supported_task_types,
            "input_requirements": input_requirements,
            "output_contract": output_contract,
            "supports_concurrency": True,
            "allows_auto_retry": True,
        },
        "input_schema": {},
        "output_schema": {},
        "timeout_seconds": 300,
        "max_retries": 1,
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


_cleanup_database()

from src.apps.api.app import app  # noqa: E402
from src.apps.api.bootstrap import ensure_builtin_agent_roles  # noqa: E402


client = TestClient(app)


def setup_function() -> None:
    _cleanup_database()
    ensure_builtin_agent_roles()


def teardown_function() -> None:
    _cleanup_database()


def test_routes_tasks_by_exact_task_type() -> None:
    suffix = uuid.uuid4().hex[:8]
    role = _register_agent(
        client,
        role_name=f"{ROUTING_PREFIX}generate-{suffix}",
        capabilities=["task:generate"],
        supported_task_types=["generate"],
    )

    response = client.post("/task-batches", json=_batch_payload("generate", suffix))

    assert response.status_code == 201
    tasks = response.json()["tasks"]
    assert all(task["assigned_agent_role"] == role["role_name"] for task in tasks)
    assert all(task["routing_reason"] == "matched by task_type=generate" for task in tasks)
    assert all(task["status"] == "queued" for task in tasks)


def test_routes_tasks_by_capability_when_task_type_not_declared() -> None:
    suffix = uuid.uuid4().hex[:8]
    role = _register_agent(
        client,
        role_name=f"{ROUTING_PREFIX}summary-{suffix}",
        capabilities=["task:write_summary"],
        supported_task_types=[],
    )

    response = client.post("/task-batches", json=_batch_payload("write_summary", suffix))

    assert response.status_code == 201
    tasks = response.json()["tasks"]
    assert all(task["assigned_agent_role"] == role["role_name"] for task in tasks)
    assert all(task["routing_reason"] == "matched by capability=task:write_summary" for task in tasks)


def test_routes_builtin_search_and_code_roles_by_capability() -> None:
    suffix = uuid.uuid4().hex[:8]

    search_response = client.post("/task-batches", json=_batch_payload("research_topic", f"{suffix}-search"))
    assert search_response.status_code == 201
    search_tasks = search_response.json()["tasks"]
    assert all(task["assigned_agent_role"] == "search_agent" for task in search_tasks)
    assert all(task["routing_reason"] == "matched by capability=task:research_topic" for task in search_tasks)

    code_response = client.post("/task-batches", json=_batch_payload("implement_feature", f"{suffix}-code"))
    assert code_response.status_code == 201
    code_tasks = code_response.json()["tasks"]
    assert all(task["assigned_agent_role"] == "code_agent" for task in code_tasks)
    assert all(task["routing_reason"] == "matched by capability=task:implement_feature" for task in code_tasks)


def test_routes_tasks_to_default_worker_as_fallback() -> None:
    suffix = uuid.uuid4().hex[:8]
    role = _register_agent(
        client,
        role_name="default_worker",
        capabilities=["default_worker"],
        supported_task_types=[],
        declare_schema=False,
    )

    response = client.post("/task-batches", json=_batch_payload("unmatched_type", suffix))

    assert response.status_code == 201
    tasks = response.json()["tasks"]
    assert all(task["assigned_agent_role"] == role["role_name"] for task in tasks)
    assert all(task["routing_reason"] == "fallback to default_worker" for task in tasks)


def test_marks_tasks_waiting_review_when_no_role_matches() -> None:
    suffix = uuid.uuid4().hex[:8]

    response = client.post("/task-batches", json=_batch_payload("no_match", suffix))

    assert response.status_code == 201
    tasks = response.json()["tasks"]
    assert all(task["assigned_agent_role"] is None for task in tasks)
    assert all(task["needs_review"] is True for task in tasks)
    assert all(task["status"] == "needs_review" for task in tasks)
    assert all(task["routing_reason"] == "No eligible agent role found for task_type=no_match" for task in tasks)

    engine = create_engine(_database_url())
    with engine.connect() as conn:
        review_count = conn.execute(
            text(
                "SELECT count(*) FROM review_checkpoints rc "
                "JOIN tasks t ON rc.task_id = t.id "
                "WHERE t.title LIKE :prefix"
            ),
            {"prefix": f"{ROUTING_PREFIX}task-{suffix}%"},
        ).scalar_one()
        assignment_count = conn.execute(
            text(
                "SELECT count(*) FROM assignments a "
                "JOIN tasks t ON a.task_id = t.id "
                "WHERE t.title LIKE :prefix"
            ),
            {"prefix": f"{ROUTING_PREFIX}task-{suffix}%"},
        ).scalar_one()

    assert review_count == 3
    assert assignment_count == 0


def test_routing_writes_status_transition_events() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(
        client,
        role_name=f"{ROUTING_PREFIX}event-{suffix}",
        capabilities=["task:generate"],
        supported_task_types=["generate"],
    )

    response = client.post("/task-batches", json=_batch_payload("generate", suffix))

    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]

    events_response = client.get(f"/tasks/{task_id}/events")
    assert events_response.status_code == 200
    events = events_response.json()
    assert len(events) == 1
    assert events[0]["event_status"] == "queued"
    assert events[0]["payload"]["from_status"] == "pending"
    assert events[0]["payload"]["to_status"] == "queued"
