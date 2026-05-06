import os
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text


ROOT = Path(__file__).resolve().parents[2]
TEST_TITLE_PREFIX = "normalization-test-batch-"

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


_cleanup_database()

from src.apps.api.app import app  # noqa: E402


client = TestClient(app)


def setup_function() -> None:
    _cleanup_database()


def teardown_function() -> None:
    _cleanup_database()


def _base_payload(tasks: list[dict]) -> dict:
    suffix = uuid.uuid4().hex[:8]
    return {
        "title": f"{TEST_TITLE_PREFIX}{suffix}",
        "description": "normalization batch",
        "created_by": "pytest",
        "metadata": {"suite": "normalization"},
        "tasks": tasks,
    }


def test_submit_returns_normalization_metadata_for_exact_deduplication() -> None:
    payload = _base_payload(
        [
            {
                "client_task_id": "task_1",
                "title": "Research topic",
                "description": "Research topic",
                "task_type": "research",
                "input_payload": {"topic": "ai"},
            },
            {
                "client_task_id": "task_2",
                "title": "Research topic",
                "description": "Research topic",
                "task_type": "research",
                "input_payload": {"topic": "ai"},
            },
            {
                "client_task_id": "task_3",
                "title": "Write summary",
                "task_type": "write_summary",
                "input_payload": {"topic": "ai"},
            },
        ]
    )

    response = client.post("/task-batches", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["original_task_count"] == 3
    assert body["normalized_task_count"] == 2
    assert len(body["tasks"]) == 2

    deduped = next(item for item in body["normalization"] if item["client_task_id"] == "task_2")
    assert deduped["action"] == "deduped"
    assert deduped["effective_client_task_id"] == "task_1"


def test_submit_merges_similar_tasks_and_keeps_more_complete_item() -> None:
    payload = _base_payload(
        [
            {
                "client_task_id": "task_1",
                "title": "Implement API",
                "description": "short",
                "task_type": "implement",
                "input_payload": {"service": "billing"},
            },
            {
                "client_task_id": "task_2",
                "title": " implement   api ",
                "description": "implement the billing API with retry and metrics",
                "task_type": "implement",
                "input_payload": {"service": "billing", "retry": True},
            },
            {
                "client_task_id": "task_3",
                "title": "Test API",
                "task_type": "test",
                "input_payload": {"service": "billing"},
            },
        ]
    )

    response = client.post("/task-batches", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["normalized_task_count"] == 2
    merged = next(item for item in body["normalization"] if item["client_task_id"] == "task_2")
    assert merged["action"] == "merged"
    assert merged["effective_client_task_id"] == "task_1"


def test_submit_fills_missing_fields_marks_ambiguous_and_infers_dependency() -> None:
    payload = _base_payload(
        [
            {
                "client_task_id": "task_1",
                "title": "研究方案",
                "task_type": "research",
                "input_payload": {"topic": "ai"},
            },
            {
                "client_task_id": "task_2",
                "title": "处理一下",
                "task_type": "unknown",
                "input_payload": {},
            },
            {
                "client_task_id": "task_3",
                "title": "Write summary",
                "task_type": "write_summary",
                "input_payload": {"topic": "ai"},
            },
        ]
    )

    response = client.post("/task-batches", json=payload)
    assert response.status_code == 201
    body = response.json()

    ambiguous = next(item for item in body["normalization"] if item["client_task_id"] == "task_2")
    assert ambiguous["is_ambiguous"] is True
    assert "description" in ambiguous["missing_fields_filled"]
    assert "expected_output_schema" in ambiguous["missing_fields_filled"]

    summary_task = next(item for item in body["normalization"] if item["client_task_id"] == "task_3")
    assert summary_task["inferred_dependency_client_task_ids"] == ["task_1"]

    created_summary = next(item for item in body["tasks"] if item["client_task_id"] == "task_3")
    assert len(created_summary["dependency_ids"]) == 1


def test_submit_normalization_does_not_create_cycle_from_inferred_dependency() -> None:
    payload = _base_payload(
        [
            {
                "client_task_id": "task_1",
                "title": "Implement service",
                "task_type": "implement",
                "input_payload": {"service": "x"},
            },
            {
                "client_task_id": "task_2",
                "title": "Test service",
                "task_type": "test",
                "input_payload": {"service": "x"},
            },
            {
                "client_task_id": "task_3",
                "title": "Review service",
                "task_type": "review",
                "input_payload": {"service": "x"},
            },
        ]
    )

    response = client.post("/task-batches", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["normalized_task_count"] == 3
    review_item = next(item for item in body["normalization"] if item["client_task_id"] == "task_3")
    assert review_item["inferred_dependency_client_task_ids"] == ["task_2"]


def test_submit_auto_task_type_returns_recognized_code_intent() -> None:
    payload = _base_payload(
        [
            {
                "client_task_id": "task_1",
                "title": "Submitted task",
                "description": "写一个判断字符串是否为空的 Go 代码，以 markdown 给我",
                "task_type": "auto",
                "input_payload": {"prompt": "写一个判断字符串是否为空的 Go 代码，以 markdown 给我"},
            }
        ]
    )

    response = client.post("/task-batches", json=payload)
    assert response.status_code == 201
    body = response.json()

    normalization = body["normalization"][0]
    assert normalization["recognized_intent"]["primary_intent"] == "coding"
    assert normalization["recognized_intent"]["task_type"] == "code"
    assert normalization["recognized_intent"]["language"] == "go"
    assert normalization["recognized_intent"]["deliverable_contract"]["expected_artifact_types"] == ["document"]
    assert normalization["recognized_intent"]["deliverable_contract"]["deliverable_type"] == "markdown"
    assert any("task_type normalized from auto to code" in note for note in normalization["notes"])

    summary_response = client.get(f"/task-batches/{body['batch_id']}/summary")
    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary["tasks"][0]["task_type"] == "code"
