from __future__ import annotations

import os
import sys
import threading
import time
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
        conn.execute(text("DELETE FROM task_batches"))
        conn.execute(text("DELETE FROM agent_roles"))


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
    prompt_budget_policy: dict | None = None,
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
    if prompt_budget_policy is not None:
        payload["prompt_budget_policy"] = prompt_budget_policy
    response = client.post("/agents/register", json=payload)
    assert response.status_code in {201, 400}
    if response.status_code == 400:
        assert response.json()["detail"] == f"Agent role {role_name} already exists"
        return {"role_name": role_name}
    return response.json()


_cleanup_database()

from src.apps.api.app import app  # noqa: E402
from src.apps.worker.executor import run_next_task  # noqa: E402
from src.apps.worker.loop import run_worker_loop  # noqa: E402
from src.apps.worker.registry import AgentRegistry, build_default_registry  # noqa: E402
from src.packages.core.db.models import ExecutionRunORM, TaskORM  # noqa: E402


client = TestClient(app)


class FailingAgent:
    def run(self, task: TaskORM, context) -> dict:
        raise RuntimeError(f"intentional failure for {task.id}")


class SlowAgent:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def run(self, task: TaskORM, context) -> dict:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.2)
            return {"status": "ok", "task_id": task.id}
        finally:
            with self._lock:
                self.active -= 1


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
        executed_task_id = run.task_id

    task_response = client.get(f"/tasks/{executed_task_id}")
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "success"
    assert task_response.json()["task_summary"]["task_id"] == executed_task_id

    runs_response = client.get(f"/tasks/{executed_task_id}/runs")
    assert runs_response.status_code == 200
    runs = runs_response.json()
    assert len(runs) == 1
    assert runs[0]["run_status"] == "success"
    assert runs[0]["output_snapshot"]["status"] == "ok"
    assert runs[0]["output_snapshot"]["task_id"] == executed_task_id
    assert runs[0]["budget_report"]["model_context_limit"] == 128000
    assert runs[0]["budget_report"]["estimated_input_tokens"] > 0
    assert runs[0]["budget_report"]["reserved_output_tokens"] >= 256
    assert runs[0]["budget_report"]["overflow_risk"] is False
    assert runs[0]["budget_report"]["initial_overflow_risk"] is False
    assert runs[0]["budget_report"]["budget_policy"]["template_name"] == "default"
    assert runs[0]["result_summary"]["status"] == "success"
    assert runs[0]["result_summary"]["output_summary"]["status"] == "ok"

    events_response = client.get(f"/tasks/{executed_task_id}/events")
    assert events_response.status_code == 200
    statuses = [
        event["event_status"]
        for event in events_response.json()
        if event["event_type"] == "task_status_changed"
    ]
    assert statuses == ["queued", "running", "success"]
    replay_snapshot = next(
        event for event in events_response.json() if event["event_type"] == "execution_run_replay_snapshot"
    )
    assert replay_snapshot["payload"]["budget_report"] == runs[0]["budget_report"]
    assert replay_snapshot["payload"]["task_summary"]["task_id"] == executed_task_id
    finished_event = next(event for event in events_response.json() if event["event_type"] == "execution_run_finished")
    assert finished_event["payload"]["result_summary"] == runs[0]["result_summary"]


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
    assert runs[0]["result_summary"]["status"] == "error"


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
        executed_task_id = run.task_id

    run_response = client.get(f"/runs/{run_id}")
    assert run_response.status_code == 200
    payload = run_response.json()
    assert payload["id"] == run_id
    assert payload["task_id"] == executed_task_id
    assert payload["run_status"] == "success"
    assert payload["result_summary"]["status"] == "success"


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
    assert tasks[0]["status"] == "queued"
    assert tasks[1]["status"] == "blocked"
    assert tasks[2]["status"] == "queued"

    engine = create_engine(_database_url())

    with Session(engine) as session:
        first_run = run_next_task(session, build_default_registry())
        assert first_run is not None

    with Session(engine) as session:
        persisted_tasks = session.query(TaskORM).filter(TaskORM.id.in_(task_ids)).all()
        status_by_id = {task.id: task.status for task in persisted_tasks}
        assert status_by_id[task_ids[0]] == "success"
        assert status_by_id[task_ids[1]] == "queued"
        assert status_by_id[task_ids[2]] == "queued"

        run_task_ids = session.query(ExecutionRunORM.task_id).all()
        assert [task_id for (task_id,) in run_task_ids] == [task_ids[0]]


def test_worker_unlocks_blocked_task_after_dependency_success() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(
        client,
        role_name="default_worker",
        capabilities=["default_worker"],
        supported_task_types=[],
        declare_schema=False,
    )

    payload = {
        "title": f"{TEST_PREFIX}unlock-batch-{suffix}",
        "description": "unlock batch",
        "created_by": "pytest",
        "metadata": {"suite": "worker-unlock"},
        "tasks": [
            {
                "client_task_id": "task_1",
                "title": f"{TEST_PREFIX}unlock-{suffix}-1",
                "task_type": "generate",
                "priority": "medium",
                "input_payload": {"text": "a"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
            {
                "client_task_id": "task_2",
                "title": f"{TEST_PREFIX}unlock-{suffix}-2",
                "task_type": "generate",
                "priority": "medium",
                "input_payload": {"text": "b"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": ["task_1"],
            },
            {
                "client_task_id": "task_3",
                "title": f"{TEST_PREFIX}unlock-{suffix}-3",
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
    dependent_task_id = tasks[1]["task_id"]
    assert tasks[1]["status"] == "blocked"

    engine = create_engine(_database_url())
    with Session(engine) as session:
        first_run = run_next_task(session, build_default_registry())
        assert first_run is not None

    task_response = client.get(f"/tasks/{dependent_task_id}")
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "queued"

    events_response = client.get(f"/tasks/{dependent_task_id}/events")
    assert events_response.status_code == 200
    statuses = [
        event["event_status"]
        for event in events_response.json()
        if event["event_type"] == "task_status_changed"
    ]
    assert statuses == ["blocked", "queued"]


def test_worker_trims_context_before_execution_when_budget_overflows() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(
        client,
        role_name="default_worker",
        capabilities=["default_worker"],
        supported_task_types=[],
        declare_schema=False,
        prompt_budget_policy={
            "template_name": "worker-trim",
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

    large_blob = "x" * 600000
    payload = {
        "title": f"{TEST_PREFIX}budget-batch-{suffix}",
        "description": "budget batch",
        "created_by": "pytest",
        "metadata": {"suite": "worker-budget"},
        "tasks": [
            {
                "client_task_id": "task_1",
                "title": f"{TEST_PREFIX}budget-{suffix}-1",
                "task_type": "generate",
                "priority": "medium",
                "input_payload": {"text": "seed"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
            {
                "client_task_id": "task_2",
                "title": f"{TEST_PREFIX}budget-{suffix}-2",
                "task_type": "generate",
                "priority": "medium",
                "input_payload": {
                    "text": large_blob,
                    "history": [{"role": "user", "content": large_blob}],
                    "dependencies": [{"artifact": large_blob}],
                },
                "expected_output_schema": {"type": "object", "properties": {"summary": {"type": "string"}}},
                "dependency_client_task_ids": ["task_1"],
            },
            {
                "client_task_id": "task_3",
                "title": f"{TEST_PREFIX}budget-{suffix}-3",
                "task_type": "generate",
                "priority": "medium",
                "input_payload": {"text": "tail"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
        ],
    }
    response = client.post("/task-batches", json=payload)
    assert response.status_code == 201
    dependent_task_id = response.json()["tasks"][1]["task_id"]

    engine = create_engine(_database_url())
    with Session(engine) as session:
        first_run = run_next_task(session, build_default_registry())
        assert first_run is not None
    with Session(engine) as session:
        second_run = run_next_task(session, build_default_registry())
        assert second_run is not None
        assert second_run.task_id == dependent_task_id

    runs_response = client.get(f"/tasks/{dependent_task_id}/runs")
    assert runs_response.status_code == 200
    run_payload = runs_response.json()[0]
    assert run_payload["budget_report"]["initial_overflow_risk"] is True
    assert run_payload["budget_report"]["overflow_risk"] is False
    assert run_payload["budget_report"]["trim_applied"] is True
    assert "removed_irrelevant_history" in run_payload["budget_report"]["trim_steps"]
    assert "removed_nonessential_dependency_payload" in run_payload["budget_report"]["trim_steps"]
    assert run_payload["budget_report"]["degradation_mode"] in {"compressed_input", "summary_only", "trimmed_dependencies"}
    assert run_payload["input_snapshot"] != payload["tasks"][1]["input_payload"]
    assert any("budget estimated" in line for line in run_payload["logs"])
    assert "dependency_summaries" in run_payload["input_snapshot"]
    assert run_payload["input_snapshot"]["dependency_summaries"][0]["result_summary"]["status"] == "success"

    events = client.get(f"/tasks/{dependent_task_id}/events").json()
    assert "context_trimmed" in [event["event_type"] for event in events]


def test_worker_moves_task_to_review_when_trimming_cannot_fit_budget() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(
        client,
        role_name="default_worker",
        capabilities=["default_worker"],
        supported_task_types=[],
        declare_schema=False,
        prompt_budget_policy={
            "template_name": "worker-impossible",
            "model_context_limit": 64,
            "max_global_background_tokens": 256,
            "max_task_input_tokens": 256,
            "max_dependency_summary_tokens": 256,
            "max_result_summary_tokens": 256,
            "max_validation_rule_tokens": 256,
            "max_history_background_tokens": 64,
            "reserved_output_tokens": 128,
        },
    )

    payload = {
        "title": f"{TEST_PREFIX}review-batch-{suffix}",
        "description": "review fallback batch",
        "created_by": "pytest",
        "metadata": {"suite": "worker-review"},
        "tasks": [
            {
                "client_task_id": "task_1",
                "title": f"{TEST_PREFIX}review-{suffix}-1",
                "task_type": "generate",
                "priority": "medium",
                "input_payload": {"text": "x" * 4000, "history": ["y" * 4000]},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
            {
                "client_task_id": "task_2",
                "title": f"{TEST_PREFIX}review-{suffix}-2",
                "task_type": "generate",
                "priority": "medium",
                "input_payload": {"text": "ok"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
            {
                "client_task_id": "task_3",
                "title": f"{TEST_PREFIX}review-{suffix}-3",
                "task_type": "generate",
                "priority": "medium",
                "input_payload": {"text": "ok"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
        ],
    }
    response = client.post("/task-batches", json=payload)
    assert response.status_code == 201
    task_id = response.json()["tasks"][0]["task_id"]

    engine = create_engine(_database_url())
    with Session(engine) as session:
        first_attempt = run_next_task(session, build_default_registry())
        assert first_attempt is None

    task_response = client.get(f"/tasks/{task_id}")
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "needs_review"

    runs_response = client.get(f"/tasks/{task_id}/runs")
    assert runs_response.status_code == 200
    assert runs_response.json() == []

    reviews_response = client.get(f"/tasks/{task_id}/reviews")
    assert reviews_response.status_code == 200
    assert len(reviews_response.json()) == 1
    assert reviews_response.json()[0]["reason_category"] == "manual_override"

    events = client.get(f"/tasks/{task_id}/events").json()
    event_types = [event["event_type"] for event in events]
    assert "review_checkpoint_created" in event_types
    status_event = [event for event in events if event["event_type"] == "task_status_changed"][-1]
    assert status_event["event_status"] == "needs_review"
    assert status_event["payload"]["source"] == "worker"


def test_worker_loop_runs_tasks_in_parallel_with_configured_limit() -> None:
    suffix = uuid.uuid4().hex[:8]
    role_name = f"{TEST_PREFIX}slow-{suffix}"
    _register_agent(
        client,
        role_name=role_name,
        capabilities=[f"task:{role_name}"],
        supported_task_types=[role_name],
    )

    response = client.post("/task-batches", json=_batch_payload(role_name, suffix))
    assert response.status_code == 201
    task_ids = [task["task_id"] for task in response.json()["tasks"]]

    slow_agent = SlowAgent()
    registry = AgentRegistry()
    registry.register(role_name, slow_agent)

    engine = create_engine(_database_url())
    processed = run_worker_loop(
        lambda: Session(engine),
        registry,
        max_concurrency=2,
        poll_interval_seconds=0.01,
        max_iterations=50,
    )

    assert processed == 3
    assert slow_agent.max_active == 2

    with Session(engine) as session:
        tasks = session.query(TaskORM).filter(TaskORM.id.in_(task_ids)).all()
        assert all(task.status == "success" for task in tasks)
