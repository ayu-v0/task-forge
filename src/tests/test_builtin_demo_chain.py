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
        conn.execute(text("DELETE FROM task_batches"))


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
from src.apps.api.bootstrap import ensure_builtin_agent_roles  # noqa: E402
from src.apps.worker.executor import run_next_task  # noqa: E402
from src.apps.worker.registry import build_default_registry  # noqa: E402

def setup_function() -> None:
    _cleanup_database()
    ensure_builtin_agent_roles()


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
            search_count = conn.execute(
                text("SELECT count(*) FROM agent_roles WHERE role_name = 'search_agent'")
            ).scalar_one()
            code_count = conn.execute(
                text("SELECT count(*) FROM agent_roles WHERE role_name = 'code_agent'")
            ).scalar_one()

        assert planner_count == 1
        assert worker_count == 1
        assert reviewer_count == 1
        assert search_count == 1
        assert code_count == 1

        planner_role = client.get("/agents").json()
        planner_entry = next(item for item in planner_role if item["role_name"] == "planner_agent")
        worker_entry = next(item for item in planner_role if item["role_name"] == "worker_agent")
        reviewer_entry = next(item for item in planner_role if item["role_name"] == "reviewer_agent")
        assert planner_entry["prompt_budget_policy"]["template_name"] == "planner"
        assert worker_entry["prompt_budget_policy"]["template_name"] == "worker"
        assert reviewer_entry["prompt_budget_policy"]["template_name"] == "reviewer"

        suffix = uuid.uuid4().hex[:8]
        create_response = client.post("/task-batches", json=_demo_payload(suffix))
        assert create_response.status_code == 201
        created = create_response.json()
        created_ids = [task["task_id"] for task in created["tasks"]]

        with Session(engine) as session:
            registry = build_default_registry()
            first_run = run_next_task(session, registry)
            second_run = run_next_task(session, registry)
            third_run = run_next_task(session, registry)
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

        run_details = []
        for summary in run_summaries:
            run_response = client.get(f"/runs/{summary['run_id']}")
            assert run_response.status_code == 200
            run_details.append(run_response.json())

        planner_run = next(item for item in run_details if item["output_snapshot"].get("stage") == "planner")
        worker_run = next(item for item in run_details if item["output_snapshot"].get("stage") == "worker")
        reviewer_run = next(item for item in run_details if item["output_snapshot"].get("stage") == "reviewer")
        assert all(item["result_summary"]["status"] == "success" for item in run_details)
        assert planner_run["budget_report"]["budget_policy"]["template_name"] == "planner"
        assert planner_run["budget_report"]["global_background_tokens"] > planner_run["budget_report"]["dependency_summary_tokens"]
        assert worker_run["budget_report"]["budget_policy"]["template_name"] == "worker"
        assert (
            worker_run["budget_report"]["budget_policy"]["max_task_input_tokens"]
            > worker_run["budget_report"]["budget_policy"]["max_dependency_summary_tokens"]
        )
        assert worker_run["budget_report"]["task_input_tokens"] > 0
        assert reviewer_run["budget_report"]["budget_policy"]["template_name"] == "reviewer"
        assert reviewer_run["budget_report"]["result_summary_tokens"] > 0
        assert reviewer_run["budget_report"]["validation_rule_tokens"] > reviewer_run["budget_report"]["history_background_tokens"]

        assert run_body["task_id"] == reviewer_task_id
        assert run_body["output_snapshot"]["stage"] == "reviewer"
        assert run_body["output_snapshot"]["validation_passed"] is True
        assert run_body["output_snapshot"]["needs_manual_review"] is False

        for task_id in created_ids:
            task_response = client.get(f"/tasks/{task_id}")
            assert task_response.status_code == 200
            assert task_response.json()["status"] == "success"
            assert task_response.json()["task_summary"]["task_id"] == task_id

            events_response = client.get(f"/tasks/{task_id}/events")
            assert events_response.status_code == 200
            statuses = [
                event["event_status"]
                for event in events_response.json()
                if event["event_type"] == "task_status_changed"
            ]
            assert statuses[-2:] == ["running", "success"]
            assert statuses[0] in {"queued", "blocked"}


def test_builtin_search_and_code_roles_execute_distinct_outputs() -> None:
    with TestClient(app) as client:
        engine = create_engine(_database_url())
        suffix = uuid.uuid4().hex[:8]
        payload = {
            "title": f"{DEMO_PREFIX}search-code-{suffix}",
            "description": "built-in search and code demo",
            "created_by": "pytest",
            "metadata": {"suite": "builtin-search-code"},
            "tasks": [
                {
                    "client_task_id": "task_1",
                    "title": f"{DEMO_PREFIX}search-{suffix}",
                    "task_type": "research_topic",
                    "priority": "medium",
                    "input_payload": {"query": "python worker patterns"},
                    "expected_output_schema": {"type": "object"},
                    "dependency_client_task_ids": [],
                },
                {
                    "client_task_id": "task_2",
                    "title": f"{DEMO_PREFIX}code-{suffix}",
                    "task_type": "implement_feature",
                    "priority": "medium",
                    "input_payload": {"prompt": "add worker retries", "language": "python"},
                    "expected_output_schema": {"type": "object"},
                    "dependency_client_task_ids": [],
                },
                {
                    "client_task_id": "task_3",
                    "title": f"{DEMO_PREFIX}filler-{suffix}",
                    "task_type": "research_topic",
                    "priority": "medium",
                    "input_payload": {"query": "queue locking strategy"},
                    "expected_output_schema": {"type": "object"},
                    "dependency_client_task_ids": [],
                },
            ],
        }

        create_response = client.post("/task-batches", json=payload)
        assert create_response.status_code == 201
        created = create_response.json()["tasks"]
        assignments = {task["title"]: task["assigned_agent_role"] for task in created}
        assert assignments[f"{DEMO_PREFIX}search-{suffix}"] == "search_agent"
        assert assignments[f"{DEMO_PREFIX}code-{suffix}"] == "code_agent"

        with Session(engine) as session:
            registry = build_default_registry()
            first_run = run_next_task(session, registry)
            second_run = run_next_task(session, registry)
            third_run = run_next_task(session, registry)
            assert first_run is not None
            assert second_run is not None
            assert third_run is not None
            run_ids = [first_run.id, second_run.id, third_run.id]

        run_payloads = []
        for run_id in run_ids:
            run_response = client.get(f"/runs/{run_id}")
            assert run_response.status_code == 200
            run_payloads.append(run_response.json())

        stages = {payload["output_snapshot"].get("stage") for payload in run_payloads}
        assert "search" in stages
        assert "code" in stages
        assert any(
            payload["output_snapshot"].get("search_plan", {}).get("intent") == "research"
            for payload in run_payloads
        )
        assert any(
            payload["output_snapshot"].get("code_plan", {}).get("language") == "python"
            for payload in run_payloads
        )
