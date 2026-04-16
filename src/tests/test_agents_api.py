from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text


TEST_ROLE_PREFIX = "test-agent-role-"
ROOT = Path(__file__).resolve().parents[2]

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
        conn.execute(
            text("DELETE FROM agent_roles WHERE role_name LIKE :prefix"),
            {"prefix": f"{TEST_ROLE_PREFIX}%"},
        )


def _payload() -> dict:
    suffix = uuid.uuid4().hex[:8]
    return {
        "role_name": f"{TEST_ROLE_PREFIX}{suffix}",
        "description": "Test agent role",
        "capabilities": ["task:generate", "retry:auto"],
        "capability_declaration": {
            "supported_task_types": ["generate"],
            "input_requirements": {"type": "object", "required": ["text"]},
            "output_contract": {"type": "object", "properties": {"summary": {"type": "string"}}},
            "supports_concurrency": True,
            "allows_auto_retry": True,
        },
        "prompt_budget_policy": {
            "template_name": "planner",
            "model_context_limit": 64000,
            "max_global_background_tokens": 2000,
            "max_task_input_tokens": 1200,
            "max_dependency_summary_tokens": 300,
            "max_result_summary_tokens": 100,
            "max_validation_rule_tokens": 800,
            "max_history_background_tokens": 150,
            "reserved_output_tokens": 900,
        },
        "input_schema": {"schema_version": "1"},
        "output_schema": {"schema_version": "1"},
        "timeout_seconds": 300,
        "max_retries": 2,
        "enabled": True,
        "version": "1.0.0",
    }


_cleanup_database()

from src.apps.api.app import app  # noqa: E402


client = TestClient(app)


def setup_function() -> None:
    _cleanup_database()


def teardown_function() -> None:
    _cleanup_database()


def test_register_list_get_and_disable_agent() -> None:
    payload = _payload()

    register_response = client.post("/agents/register", json=payload)
    assert register_response.status_code == 201
    registered = register_response.json()
    assert registered["role_name"] == payload["role_name"]
    assert registered["capability_declaration"]["supported_task_types"] == ["generate"]
    assert registered["capability_declaration"]["supports_concurrency"] is True
    assert registered["prompt_budget_policy"]["template_name"] == "planner"
    assert registered["prompt_budget_policy"]["max_global_background_tokens"] == 2000
    assert registered["enabled"] is True

    list_response = client.get("/agents")
    assert list_response.status_code == 200
    listed_roles = list_response.json()
    assert any(role["id"] == registered["id"] for role in listed_roles)

    get_response = client.get(f"/agents/{registered['id']}")
    assert get_response.status_code == 200
    fetched = get_response.json()
    assert fetched["role_name"] == payload["role_name"]
    assert fetched["input_schema"]["schema_version"] == "1"
    assert fetched["prompt_budget_policy"]["reserved_output_tokens"] == 900

    patch_response = client.patch(
        f"/agents/{registered['id']}",
        json={
            "enabled": False,
            "description": "Disabled role",
            "prompt_budget_policy": {
                "template_name": "reviewer",
                "model_context_limit": 96000,
                "max_global_background_tokens": 300,
                "max_task_input_tokens": 600,
                "max_dependency_summary_tokens": 200,
                "max_result_summary_tokens": 2400,
                "max_validation_rule_tokens": 1800,
                "max_history_background_tokens": 50,
                "reserved_output_tokens": 700,
            },
        },
    )
    assert patch_response.status_code == 200
    patched = patch_response.json()
    assert patched["enabled"] is False
    assert patched["description"] == "Disabled role"
    assert patched["prompt_budget_policy"]["template_name"] == "reviewer"
    assert patched["prompt_budget_policy"]["max_result_summary_tokens"] == 2400

    get_after_patch = client.get(f"/agents/{registered['id']}")
    assert get_after_patch.status_code == 200
    assert get_after_patch.json()["enabled"] is False
    assert get_after_patch.json()["prompt_budget_policy"]["reserved_output_tokens"] == 700


def test_register_rejects_duplicate_role_name() -> None:
    payload = _payload()

    first_response = client.post("/agents/register", json=payload)
    assert first_response.status_code == 201

    second_response = client.post("/agents/register", json=payload)
    assert second_response.status_code == 400
    assert second_response.json()["detail"] == f"Agent role {payload['role_name']} already exists"


def test_get_unknown_agent_returns_404() -> None:
    response = client.get("/agents/not_found")

    assert response.status_code == 404
    assert response.json()["detail"] == "Agent role not found"


def test_patch_unknown_agent_returns_404() -> None:
    response = client.patch("/agents/not_found", json={"enabled": False})

    assert response.status_code == 404
    assert response.json()["detail"] == "Agent role not found"
