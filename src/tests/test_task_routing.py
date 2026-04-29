from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.packages.core.db.models import ExecutionRunORM, TaskBatchORM, TaskORM


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


def _batch_payload(
    task_type: str,
    suffix: str,
    *,
    input_payload: dict | None = None,
    expected_output_schema: dict | None = None,
) -> dict:
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
                "input_payload": input_payload or {"text": "hello"},
                "expected_output_schema": expected_output_schema or {"type": "object"},
                "dependency_client_task_ids": [],
            },
            {
                "client_task_id": "task_2",
                "title": f"{ROUTING_PREFIX}task-{suffix}-2",
                "task_type": task_type,
                "priority": "medium",
                "input_payload": input_payload or {"text": "world"},
                "expected_output_schema": expected_output_schema or {"type": "object"},
                "dependency_client_task_ids": [],
            },
            {
                "client_task_id": "task_3",
                "title": f"{ROUTING_PREFIX}task-{suffix}-3",
                "task_type": task_type,
                "priority": "medium",
                "input_payload": input_payload or {"text": "!"},
                "expected_output_schema": expected_output_schema or {"type": "object"},
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
    timeout_seconds: int = 300,
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
        "timeout_seconds": timeout_seconds,
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


def _seed_history(role_id: str, role_name: str, *, run_statuses: list[str], suffix: str, prompt_tokens: int = 10, completion_tokens: int = 5, latency_ms: int = 100) -> None:
    engine = create_engine(_database_url())
    with Session(engine) as session:
        batch = TaskBatchORM(
            title=f"{ROUTING_PREFIX}history-batch-{suffix}-{role_name}",
            description="routing seeded batch",
            created_by="pytest",
            status="submitted",
            total_tasks=1,
        )
        session.add(batch)
        session.flush()
        task = TaskORM(
            batch_id=batch.id,
            title=f"{ROUTING_PREFIX}history-task-{suffix}-{role_name}",
            description="routing seeded task",
            task_type="generate",
            priority="medium",
            status="success",
            input_payload={},
            expected_output_schema={},
            assigned_agent_role=role_name,
            dependency_ids=[],
            retry_count=0,
        )
        session.add(task)
        session.flush()
        for index, run_status in enumerate(run_statuses, start=1):
            session.add(
                ExecutionRunORM(
                    id=f"run_{uuid.uuid4().hex}",
                    task_id=task.id,
                    agent_role_id=role_id,
                    run_status=run_status,
                    logs=[],
                    input_snapshot={},
                    output_snapshot={},
                    token_usage={
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": prompt_tokens + completion_tokens,
                    },
                    latency_ms=latency_ms * index,
                )
            )
        session.commit()


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
    assert all("capability-ranked route selected role=" in task["routing_reason"] for task in tasks)
    assert all("via task_type,capability,schema" in task["routing_reason"] for task in tasks)
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
    assert all("via capability,schema" in task["routing_reason"] for task in tasks)


def test_routes_builtin_search_and_code_roles_by_capability() -> None:
    suffix = uuid.uuid4().hex[:8]

    search_response = client.post(
        "/task-batches",
        json=_batch_payload("research_topic", f"{suffix}-search", input_payload={"query": "topic"}),
    )
    assert search_response.status_code == 201
    search_tasks = search_response.json()["tasks"]
    assert all(task["assigned_agent_role"] == "search_agent" for task in search_tasks)
    assert all("via capability,schema" in task["routing_reason"] for task in search_tasks)

    code_response = client.post("/task-batches", json=_batch_payload("implement_feature", f"{suffix}-code", input_payload={"prompt": "do it", "language": "python"}))
    assert code_response.status_code == 201
    code_tasks = code_response.json()["tasks"]
    assert all(task["assigned_agent_role"] == "code_agent" for task in code_tasks)
    assert all("via task_type,capability,schema" in task["routing_reason"] for task in code_tasks)


def test_prefers_higher_success_rate_when_multiple_roles_match_same_task() -> None:
    suffix = uuid.uuid4().hex[:8]
    stable_role = _register_agent(
        client,
        role_name=f"{ROUTING_PREFIX}stable-{suffix}",
        capabilities=["task:generate"],
        supported_task_types=["generate"],
    )
    flaky_role = _register_agent(
        client,
        role_name=f"{ROUTING_PREFIX}flaky-{suffix}",
        capabilities=["task:generate"],
        supported_task_types=["generate"],
    )
    _seed_history(stable_role["id"], stable_role["role_name"], run_statuses=["success", "success", "failed"], suffix=suffix)
    _seed_history(flaky_role["id"], flaky_role["role_name"], run_statuses=["success", "failed", "failed"], suffix=suffix)

    response = client.post("/task-batches", json=_batch_payload("generate", suffix))

    assert response.status_code == 201
    tasks = response.json()["tasks"]
    assert all(task["assigned_agent_role"] == stable_role["role_name"] for task in tasks)
    assert all("success_rate=66.67%" in task["routing_reason"] for task in tasks)


def test_prefers_lower_cost_for_low_cost_hint_when_roles_otherwise_match() -> None:
    suffix = uuid.uuid4().hex[:8]
    cheap_role = _register_agent(
        client,
        role_name=f"{ROUTING_PREFIX}cheap-{suffix}",
        capabilities=["task:generate"],
        supported_task_types=["generate"],
    )
    costly_role = _register_agent(
        client,
        role_name=f"{ROUTING_PREFIX}costly-{suffix}",
        capabilities=["task:generate"],
        supported_task_types=["generate"],
    )
    _seed_history(cheap_role["id"], cheap_role["role_name"], run_statuses=["success"], suffix=suffix, prompt_tokens=10, completion_tokens=5)
    _seed_history(costly_role["id"], costly_role["role_name"], run_statuses=["success"], suffix=suffix, prompt_tokens=500, completion_tokens=500)

    response = client.post(
        "/task-batches",
        json=_batch_payload("generate", suffix, input_payload={"text": "hello", "cost_hint": "low"}),
    )

    assert response.status_code == 201
    tasks = response.json()["tasks"]
    assert all(task["assigned_agent_role"] == cheap_role["role_name"] for task in tasks)


def test_filters_roles_that_cannot_meet_timeout_requirement() -> None:
    suffix = uuid.uuid4().hex[:8]
    short_role = _register_agent(
        client,
        role_name=f"{ROUTING_PREFIX}short-{suffix}",
        capabilities=["task:generate"],
        supported_task_types=["generate"],
        timeout_seconds=30,
    )
    long_role = _register_agent(
        client,
        role_name=f"{ROUTING_PREFIX}long-{suffix}",
        capabilities=["task:generate"],
        supported_task_types=["generate"],
        timeout_seconds=600,
    )

    response = client.post(
        "/task-batches",
        json=_batch_payload("generate", suffix, input_payload={"text": "hello", "timeout_seconds": 120}),
    )

    assert response.status_code == 201
    tasks = response.json()["tasks"]
    assert all(task["assigned_agent_role"] == long_role["role_name"] for task in tasks)
    assert all(task["assigned_agent_role"] != short_role["role_name"] for task in tasks)


def test_marks_tasks_waiting_review_when_all_candidates_filtered_out() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(
        client,
        role_name=f"{ROUTING_PREFIX}short-{suffix}",
        capabilities=["task:generate"],
        supported_task_types=["generate"],
        timeout_seconds=30,
    )

    response = client.post(
        "/task-batches",
        json=_batch_payload("generate", suffix, input_payload={"text": "hello", "timeout_seconds": 120}),
    )

    assert response.status_code == 201
    tasks = response.json()["tasks"]
    assert all(task["assigned_agent_role"] is None for task in tasks)
    assert all(task["needs_review"] is True for task in tasks)
    assert all(task["status"] == "needs_review" for task in tasks)
    assert all(task["routing_reason"] == "No eligible agent role found for task_type=generate" for task in tasks)


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
    assert all("via default_worker" in task["routing_reason"] for task in tasks)


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
