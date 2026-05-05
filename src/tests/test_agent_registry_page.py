from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.packages.core.db.models import ExecutionRunORM


ROOT = Path(__file__).resolve().parents[2]
TEST_ROLE_PREFIX = "registry-test-role-"
TEST_BATCH_PREFIX = "registry-test-batch-"

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
        conn.execute(
            text(
                """
                DELETE FROM task_batches
                WHERE title LIKE :batch_prefix
                """
            ),
            {"batch_prefix": f"{TEST_BATCH_PREFIX}%"},
        )
        conn.execute(
            text(
                """
                DELETE FROM agent_roles
                WHERE role_name LIKE :role_prefix
                """
            ),
            {"role_prefix": f"{TEST_ROLE_PREFIX}%"},
        )


def _register_agent(
    client: TestClient,
    *,
    role_name: str,
    supported_task_types: list[str],
    enabled: bool = True,
) -> dict:
    payload = {
        "role_name": role_name,
        "description": "registry test role",
        "capabilities": [f"task:{role_name}", "task:registry"],
        "capability_declaration": {
            "supported_task_types": supported_task_types,
            "input_requirements": {"type": "object", "properties": {"text": {"type": "string"}}},
            "output_contract": {"type": "object", "properties": {"summary": {"type": "string"}}},
            "supports_concurrency": True,
            "allows_auto_retry": False,
        },
        "input_schema": {"schema_version": "1"},
        "output_schema": {"schema_version": "1"},
        "timeout_seconds": 300,
        "max_retries": 0,
        "enabled": enabled,
        "version": "1.0.0",
    }
    response = client.post("/agents/register", json=payload)
    assert response.status_code == 201
    return response.json()


def _create_batch_with_task(client: TestClient, *, task_type: str, suffix: str) -> str:
    response = client.post(
        "/task-batches",
        json={
            "title": f"{TEST_BATCH_PREFIX}{suffix}",
            "description": "registry test batch",
            "created_by": "pytest",
            "metadata": {"suite": "agent-registry"},
            "tasks": [
                {
                    "client_task_id": "task_1",
                    "title": f"registry task {suffix} 1",
                    "task_type": task_type,
                    "priority": "medium",
                    "input_payload": {"text": "alpha"},
                    "expected_output_schema": {"type": "object"},
                    "dependency_client_task_ids": [],
                },
                {
                    "client_task_id": "task_2",
                    "title": f"registry task {suffix} 2",
                    "task_type": task_type,
                    "priority": "medium",
                    "input_payload": {"text": "beta"},
                    "expected_output_schema": {"type": "object"},
                    "dependency_client_task_ids": [],
                },
                {
                    "client_task_id": "task_3",
                    "title": f"registry task {suffix} 3",
                    "task_type": task_type,
                    "priority": "medium",
                    "input_payload": {"text": "gamma"},
                    "expected_output_schema": {"type": "object"},
                    "dependency_client_task_ids": [],
                },
            ],
        },
    )
    assert response.status_code == 201
    return response.json()["tasks"][0]["task_id"]


def _insert_runs(task_id: str, agent_role_id: str, run_statuses: list[str]) -> None:
    engine = create_engine(_database_url())
    with Session(engine) as session:
        for index, run_status in enumerate(run_statuses, start=1):
            session.add(
                ExecutionRunORM(
                    id=f"run_{uuid.uuid4().hex}",
                    task_id=task_id,
                    agent_role_id=agent_role_id,
                    run_status=run_status,
                    logs=[],
                    input_snapshot={},
                    output_snapshot={},
                    token_usage=(
                        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
                        if index == 1
                        else {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}
                    ),
                    latency_ms=100 * index,
                )
            )
        session.commit()


def _seed_registry_history(*, suffix: str, run_statuses: list[str]) -> dict:
    batch_id = f"batch_{uuid.uuid4().hex}"
    task_id = f"task_{uuid.uuid4().hex}"
    role = _register_agent(
        client,
        role_name=f"{TEST_ROLE_PREFIX}{suffix}",
        supported_task_types=["registry_success_case"],
    )
    role_id = role["id"]
    role_name = role["role_name"]

    engine = create_engine(_database_url())
    with Session(engine) as session:
        session.execute(
            text(
                """
                INSERT INTO task_batches (
                    id,
                    title,
                    description,
                    created_by,
                    created_at,
                    status,
                    total_tasks,
                    metadata
                ) VALUES (
                    :id,
                    :title,
                    :description,
                    'pytest',
                    NOW(),
                    'submitted',
                    1,
                    '{}'::jsonb
                )
                """
            ),
            {
                "id": batch_id,
                "title": f"{TEST_BATCH_PREFIX}{suffix}",
                "description": "registry seeded batch",
            },
        )
        session.execute(
            text(
                """
                INSERT INTO tasks (
                    id,
                    batch_id,
                    title,
                    description,
                    task_type,
                    priority,
                    status,
                    input_payload,
                    expected_output_schema,
                    assigned_agent_role,
                    dependency_ids,
                    retry_count,
                    cancellation_requested,
                    cancellation_requested_at,
                    cancellation_reason,
                    created_at,
                    updated_at
                ) VALUES (
                    :id,
                    :batch_id,
                    :title,
                    :description,
                    'registry_success_case',
                    'medium',
                    'success',
                    '{}'::jsonb,
                    '{}'::jsonb,
                    :assigned_agent_role,
                    ARRAY[]::varchar[],
                    0,
                    FALSE,
                    NULL,
                    NULL,
                    NOW(),
                    NOW()
                )
                """
            ),
            {
                "id": task_id,
                "batch_id": batch_id,
                "title": f"registry task {suffix}",
                "description": "registry seeded task",
                "assigned_agent_role": role_name,
            },
        )
        session.commit()

    _insert_runs(task_id, role_id, run_statuses)

    return role


_cleanup_database()

from src.apps.api.app import app  # noqa: E402


client = TestClient(app)


def setup_function() -> None:
    _cleanup_database()


def teardown_function() -> None:
    _cleanup_database()


def test_agent_registry_aggregates_run_history_and_success_rate() -> None:
    suffix = uuid.uuid4().hex[:8]
    role = _seed_registry_history(suffix=suffix, run_statuses=["success", "failed", "cancelled"])

    response = client.get("/agents/registry")
    assert response.status_code == 200
    payload = response.json()
    registry_item = next(item for item in payload["items"] if item["id"] == role["id"])

    assert registry_item["role_name"] == role["role_name"]
    assert registry_item["total_runs"] == 3
    assert registry_item["success_runs"] == 1
    assert registry_item["success_rate"] == 33.33
    assert registry_item["average_latency_ms"] == 200.0
    assert registry_item["retry_rate"] == 100.0
    assert registry_item["total_tokens"] == 75
    assert registry_item["average_total_tokens"] == 25.0
    assert registry_item["total_cost_estimate"] == 0.0001
    assert registry_item["average_cost_estimate"] == 0.000033


def test_agent_registry_diagnosis_distinguishes_enabled_disabled_and_missing_matches() -> None:
    suffix = uuid.uuid4().hex[:8]
    enabled_role = _register_agent(
        client,
        role_name=f"{TEST_ROLE_PREFIX}enabled-{suffix}",
        supported_task_types=["match_enabled_case"],
    )
    disabled_role = _register_agent(
        client,
        role_name=f"{TEST_ROLE_PREFIX}disabled-{suffix}",
        supported_task_types=["match_disabled_case"],
        enabled=False,
    )

    enabled_response = client.get("/agents/registry", params={"task_type": "match_enabled_case"})
    assert enabled_response.status_code == 200
    enabled_diagnosis = enabled_response.json()["diagnosis"]
    assert enabled_diagnosis["status"] == "matched_enabled"
    assert enabled_diagnosis["matching_enabled_roles"] == [enabled_role["role_name"]]
    assert enabled_diagnosis["matching_disabled_roles"] == []

    disabled_response = client.get("/agents/registry", params={"task_type": "match_disabled_case"})
    assert disabled_response.status_code == 200
    disabled_diagnosis = disabled_response.json()["diagnosis"]
    assert disabled_diagnosis["status"] == "matched_disabled_only"
    assert disabled_diagnosis["matching_enabled_roles"] == []
    assert disabled_diagnosis["matching_disabled_roles"] == [disabled_role["role_name"]]

    missing_response = client.get("/agents/registry", params={"task_type": "totally_missing_case"})
    assert missing_response.status_code == 200
    missing_diagnosis = missing_response.json()["diagnosis"]
    assert missing_diagnosis["status"] == "no_match"
    assert missing_diagnosis["matching_enabled_roles"] == []
    assert missing_diagnosis["matching_disabled_roles"] == []


def test_agent_registry_reports_no_run_history_when_role_has_no_runs() -> None:
    suffix = uuid.uuid4().hex[:8]
    role = _register_agent(
        client,
        role_name=f"{TEST_ROLE_PREFIX}no-runs-{suffix}",
        supported_task_types=["no_runs_case"],
    )

    response = client.get("/agents/registry")
    assert response.status_code == 200
    registry_item = next(item for item in response.json()["items"] if item["id"] == role["id"])

    assert registry_item["total_runs"] == 0
    assert registry_item["success_runs"] == 0
    assert registry_item["success_rate"] is None
    assert registry_item["average_latency_ms"] is None
    assert registry_item["retry_rate"] is None
    assert registry_item["total_cost_estimate"] == 0


def test_console_agent_registry_page_is_accessible() -> None:
    response = client.get("/console/agents")
    assert response.status_code == 200
    assert "Agent Registry" in response.text
    assert "/console/vue/" in response.text


def test_root_route_serves_agent_registry_home_page() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Agent Registry" in response.text
    assert "/console/vue/" in response.text


def test_agent_registry_vue_source_includes_required_drawer_interactions() -> None:
    component_path = ROOT / "src" / "apps" / "web" / "vue" / "src" / "AgentRegistry.vue"
    component_source = component_path.read_text(encoding="utf-8")

    assert "<script setup>" in component_source
    assert "Agent角色管理" in component_source
    assert "角色列表" in component_source
    assert "openDrawer" in component_source
    assert "closeDrawer" in component_source
    assert "isSideMenuOpen" in component_source
    assert "toggleSideMenu" in component_source
    assert "openRolesFromSideMenu" in component_source
    assert "console-side-nav" in component_source
    assert "Console navigation" in component_source
    assert "Expand console menu" in component_source
    assert "Batch Console" in component_source
    assert component_source.count("Open Agent Roles") == 1
    assert "hero-actions" not in component_source
    assert "keydown" in component_source
    assert "statusFilter" in component_source
    assert "Success rate" in component_source
    assert "Avg latency" in component_source
    assert "Total cost estimate" in component_source
    assert "Why no suitable role?" in component_source
    assert "No run history" in component_source


def test_agent_registry_vue_source_uses_premium_black_purple_theme() -> None:
    component_path = ROOT / "src" / "apps" / "web" / "vue" / "src" / "AgentRegistry.vue"
    component_source = component_path.read_text(encoding="utf-8")

    assert "<style scoped>" in component_source
    assert "side-nav-toggle" in component_source
    assert "side-nav-item" in component_source
    assert "Role List" in component_source
    assert "#6366f1" in component_source
    assert "#8b5cf6" in component_source
    assert "#a855f7" in component_source
    assert "radial-gradient(circle at 50% 0%" in component_source
    assert "backdrop-filter: blur(18px)" in component_source


def test_agent_registry_built_css_does_not_disable_body_interaction() -> None:
    css_assets = list((ROOT / "src" / "apps" / "web" / "dist" / "assets").glob("*.css"))
    assert css_assets

    built_css = "\n".join(path.read_text(encoding="utf-8") for path in css_assets)
    assert "body{position:fixed" not in built_css
    assert "body{margin:0" in built_css
