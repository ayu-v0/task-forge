from __future__ import annotations

from datetime import datetime, timezone

from src.apps.worker import http_model_agent
from src.apps.worker.http_model_agent import _normalize_payload, run_model_agent_if_enabled
from src.apps.worker.types import WorkerContext
from src.packages.core.db.models import TaskORM


def test_normalize_payload_defaults_optional_model_fields() -> None:
    task = TaskORM(
        id="task_1",
        batch_id="batch_1",
        title="write hello world",
        task_type="code",
        input_payload={"prompt": "write hello world", "language": "python"},
        expected_output_schema={"type": "object"},
    )
    context = WorkerContext(
        run_id="run_1",
        task_id="task_1",
        agent_role_name="code_agent",
        started_at=datetime.now(timezone.utc),
        cancellation_check=lambda: False,
    )

    payload = _normalize_payload(
        "code_agent",
        task,
        context,
        {
            "status": "success",
            "summary": "generated hello world",
            "result": {"code": "print('Hello, World!')\n", "language": "python"},
        },
    )

    assert payload["warnings"] == []
    assert payload["next_action_hint"] is None
    assert payload["result"]["code"] == "print('Hello, World!')\n"
    assert payload["stage"] == "code"


def test_run_model_agent_wraps_non_json_document_output(monkeypatch) -> None:
    task = TaskORM(
        id="task_essay",
        batch_id="batch_1",
        title="写作文",
        task_type="worker_execute",
        input_payload={
            "prompt": "写一篇800字的作文。作文内容是：我的母亲",
            "deliverable_contract": {
                "expected_artifact_types": ["document"],
                "presentation_format": "markdown",
                "file_extension": ".md",
                "include_code_block": False,
                "require_file_level_artifact": False,
                "allow_primary_json_only": False,
            },
        },
        expected_output_schema={"type": "object"},
    )
    context = WorkerContext(
        run_id="run_1",
        task_id="task_essay",
        agent_role_name="worker_agent",
        started_at=datetime.now(timezone.utc),
        cancellation_check=lambda: False,
    )

    monkeypatch.setattr(
        http_model_agent,
        "resolve_model_request_config",
        lambda role_name: {
            "enabled": True,
            "request_format": "openai_chat_completions",
            "url": "http://model.local/v1/chat/completions",
            "headers": {},
            "model": "worker-test",
            "temperature": 0,
            "max_tokens": 800,
            "timeout_seconds": 1,
            "extra_body": {},
        },
    )

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "我的母亲是一位平凡而伟大的女性。\n她总是在我需要时出现。"
                        }
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 9, "total_tokens": 12},
            }

    monkeypatch.setattr(http_model_agent.httpx, "post", lambda *_, **__: Response())

    payload = run_model_agent_if_enabled("worker_agent", task, context)

    assert payload is not None
    assert payload["status"] == "success"
    assert "model_output_wrapped_from_non_json" in payload["warnings"]
    deliverable = payload["result"]["deliverables"][0]
    assert deliverable["type"] == "document"
    assert deliverable["path"] == "generated/task_essay.md"
    assert "我的母亲" in deliverable["content"]


def test_run_model_agent_extracts_content_field_from_malformed_json(monkeypatch) -> None:
    task = TaskORM(
        id="task_essay",
        batch_id="batch_1",
        title="写作文",
        task_type="worker_execute",
        input_payload={
            "prompt": "写作文",
            "deliverable_contract": {
                "expected_artifact_types": ["document"],
                "presentation_format": "markdown",
            },
        },
        expected_output_schema={"type": "object"},
    )
    context = WorkerContext(
        run_id="run_1",
        task_id="task_essay",
        agent_role_name="worker_agent",
        started_at=datetime.now(timezone.utc),
        cancellation_check=lambda: False,
    )
    monkeypatch.setattr(
        http_model_agent,
        "resolve_model_request_config",
        lambda role_name: {
            "enabled": True,
            "request_format": "openai_chat_completions",
            "url": "http://model.local/v1/chat/completions",
            "headers": {},
            "model": "worker-test",
            "temperature": 0,
            "max_tokens": 800,
            "timeout_seconds": 1,
            "extra_body": {},
        },
    )

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"status":"success","summary":"ok","result":{"content":"我的母亲\\n她很温柔"} "warnings":[]}'
                        }
                    }
                ]
            }

    monkeypatch.setattr(http_model_agent.httpx, "post", lambda *_, **__: Response())

    payload = run_model_agent_if_enabled("worker_agent", task, context)

    assert payload is not None
    assert payload["result"]["deliverables"][0]["content"] == "我的母亲\n她很温柔"
