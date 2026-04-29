from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[2]
TEST_PREFIX = "artifact-api-test-"

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
        "description": "artifact role",
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
        "description": "artifact batch",
        "created_by": "pytest",
        "metadata": {"suite": "artifact"},
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


_cleanup_database()

from src.apps.api.app import app  # noqa: E402
from src.apps.worker.executor import run_next_task  # noqa: E402
from src.apps.worker.registry import AgentRegistry, build_default_registry  # noqa: E402
from src.packages.core.db.models import TaskORM  # noqa: E402


client = TestClient(app)


class CodeFileAgent:
    def run(self, task: TaskORM, context) -> dict:
        return {
            "status": "ok",
            "summary": "created code file",
            "result": {
                "deliverables": [
                    {
                        "type": "code_file",
                        "path": "src/example.py",
                        "language": "python",
                        "change_type": "created",
                        "content": "print('example')\n",
                    }
                ]
            },
            "warnings": [],
            "next_action_hint": "review src/example.py",
        }


def setup_function() -> None:
    _cleanup_database()


def teardown_function() -> None:
    _cleanup_database()


def test_artifact_detail_returns_structured_primary_output() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(client, role_name="default_worker", supported_task_types=[])
    response = client.post("/task-batches", json=_batch_payload("generate", suffix))
    assert response.status_code == 201
    batch_id = response.json()["batch_id"]

    engine = create_engine(_database_url())
    with Session(engine) as session:
        run = run_next_task(session, build_default_registry())
        assert run is not None

    summary_response = client.get(f"/task-batches/{batch_id}/summary")
    assert summary_response.status_code == 200
    artifact = summary_response.json()["artifacts"][0]

    artifact_response = client.get(f"/artifacts/{artifact['artifact_id']}")
    assert artifact_response.status_code == 200
    payload = artifact_response.json()
    assert payload["artifact_type"] == "json"
    assert payload["schema_version"] == "artifact.v1"
    assert payload["raw_content"]["status"] == "ok"
    assert payload["summary"]["status"] == "success"
    assert payload["structured_output"]["field_count"] >= 1
    assert payload["metadata"]["artifact_role"] == "primary_output"


def test_artifact_detail_returns_code_file_deliverable() -> None:
    suffix = uuid.uuid4().hex[:8]
    role_name = f"{TEST_PREFIX}code-file-{suffix}"
    _register_agent(client, role_name=role_name, supported_task_types=[role_name])
    response = client.post("/task-batches", json=_batch_payload(role_name, suffix))
    assert response.status_code == 201
    batch_id = response.json()["batch_id"]

    registry = AgentRegistry()
    registry.register(role_name, CodeFileAgent())
    engine = create_engine(_database_url())
    with Session(engine) as session:
        run = run_next_task(session, registry)
        assert run is not None

    summary_response = client.get(f"/task-batches/{batch_id}/summary")
    assert summary_response.status_code == 200
    artifact = next(item for item in summary_response.json()["artifacts"] if item["artifact_type"] == "code_file")

    artifact_response = client.get(f"/artifacts/{artifact['artifact_id']}")
    assert artifact_response.status_code == 200
    payload = artifact_response.json()
    assert payload["artifact_type"] == "code_file"
    assert payload["uri"] == "workspace://src/example.py"
    assert payload["summary"]["path"] == "src/example.py"
    assert payload["summary"]["language"] == "python"
    assert payload["raw_content"]["content"] == "print('example')\n"
    assert payload["metadata"]["artifact_role"] == "final_deliverable"


def test_artifact_detail_returns_404_for_unknown_artifact() -> None:
    assert client.get("/artifacts/not-found").status_code == 404
