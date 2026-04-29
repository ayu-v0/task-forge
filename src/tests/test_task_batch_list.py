from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[2]
TEST_PREFIX = "batch-list-test-"

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


def _register_agent(client: TestClient, *, role_name: str, supported_task_types: list[str]) -> None:
    payload = {
        "role_name": role_name,
        "description": "batch list role",
        "capabilities": [f"task:{task_type}" for task_type in supported_task_types] or ["default_worker"],
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


def _batch_payload(title: str, task_type: str) -> dict:
    return {
        "title": title,
        "description": "batch list batch",
        "created_by": "pytest",
        "metadata": {"suite": "batch-list"},
        "tasks": [
            {
                "client_task_id": "task_1",
                "title": f"{title}-1",
                "task_type": task_type,
                "priority": "medium",
                "input_payload": {"text": "alpha"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
            {
                "client_task_id": "task_2",
                "title": f"{title}-2",
                "task_type": task_type,
                "priority": "medium",
                "input_payload": {"text": "beta"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
            {
                "client_task_id": "task_3",
                "title": f"{title}-3",
                "task_type": task_type,
                "priority": "medium",
                "input_payload": {"text": "gamma"},
                "expected_output_schema": {"type": "object"},
                "dependency_client_task_ids": [],
            },
        ],
    }


_cleanup_database()

from src.apps.api.app import app  # noqa: E402


client = TestClient(app)


def setup_function() -> None:
    _cleanup_database()


def teardown_function() -> None:
    _cleanup_database()


def test_list_task_batches_returns_summary_fields() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(client, role_name=f"{TEST_PREFIX}generate-{suffix}", supported_task_types=["generate"])

    response = client.post("/task-batches", json=_batch_payload(f"{TEST_PREFIX}alpha-{suffix}", "generate"))
    assert response.status_code == 201

    list_response = client.get("/task-batches")
    assert list_response.status_code == 200
    items = list_response.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["title"] == f"{TEST_PREFIX}alpha-{suffix}"
    assert item["total_tasks"] == 3
    assert item["derived_status"] == "pending"
    assert item["success_rate"] == 0.0
    assert "updated_at" in item


def test_list_task_batches_filters_by_derived_status() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(client, role_name="default_worker", supported_task_types=[])

    pending_response = client.post("/task-batches", json=_batch_payload(f"{TEST_PREFIX}pending-{suffix}", "generate"))
    assert pending_response.status_code == 201

    pending_list = client.get("/task-batches", params={"status": "pending"})
    assert pending_list.status_code == 200
    assert all(item["derived_status"] == "pending" for item in pending_list.json()["items"])

    _cleanup_database()
    review_response = client.post("/task-batches", json=_batch_payload(f"{TEST_PREFIX}review-{suffix}", "no_match"))
    assert review_response.status_code == 201

    review_list = client.get("/task-batches", params={"status": "needs_review"})
    assert review_list.status_code == 200
    assert len(review_list.json()["items"]) == 1
    assert review_list.json()["items"][0]["title"] == f"{TEST_PREFIX}review-{suffix}"


def test_list_task_batches_supports_search_and_sort() -> None:
    suffix = uuid.uuid4().hex[:8]
    _register_agent(client, role_name=f"{TEST_PREFIX}generate-{suffix}", supported_task_types=["generate"])

    first = client.post("/task-batches", json=_batch_payload(f"{TEST_PREFIX}zeta-{suffix}", "generate"))
    assert first.status_code == 201
    second = client.post("/task-batches", json=_batch_payload(f"{TEST_PREFIX}alpha-{suffix}", "generate"))
    assert second.status_code == 201

    search_response = client.get("/task-batches", params={"search": "alpha"})
    assert search_response.status_code == 200
    assert len(search_response.json()["items"]) == 1
    assert search_response.json()["items"][0]["title"] == f"{TEST_PREFIX}alpha-{suffix}"

    sort_response = client.get("/task-batches", params={"sort": "created_at_asc"})
    assert sort_response.status_code == 200
    titles = [item["title"] for item in sort_response.json()["items"]]
    assert titles == [f"{TEST_PREFIX}zeta-{suffix}", f"{TEST_PREFIX}alpha-{suffix}"]


def test_list_task_batches_returns_empty_list_when_no_batches_exist() -> None:
    response = client.get("/task-batches")
    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_console_batches_page_is_accessible() -> None:
    response = client.get("/console/batches")
    assert response.status_code == 200
    assert "Batch Console" in response.text


def test_console_batches_page_includes_embedded_detail_panel() -> None:
    response = client.get("/console/batches")
    assert response.status_code == 200
    assert 'id="batch-detail-overlay"' in response.text
    assert 'id="batch-detail-panel"' in response.text
    assert 'id="detail-body"' in response.text


def test_console_batches_assets_open_detail_in_panel() -> None:
    response = client.get("/console/assets/app.js")
    assert response.status_code == 200
    assert "/console/batches/${item.batch_id}" not in response.text
    assert "openBatchDetail" in response.text
    assert "closeBatchDetail" in response.text
    assert 'data-batch-id="${escapeHtml(item.batch_id)}"' in response.text


def test_console_batches_assets_load_task_timeline_and_artifacts() -> None:
    response = client.get("/console/assets/app.js")
    assert response.status_code == 200
    assert "/task-batches/${batchId}/summary" in response.text
    assert "/tasks/${task.task_id}/timeline" in response.text
    assert "renderArtifacts" in response.text
    assert "formatArtifactPreview" in response.text
    assert "renderCodeFileArtifact" in response.text
    assert "renderCodePatchArtifact" in response.text
    assert "renderTestReportArtifact" in response.text
    assert "renderGenericArtifact" in response.text
    assert "This code task did not produce file-level deliverables." in response.text
    assert "structured_output" in response.text
    assert "raw_content" in response.text
