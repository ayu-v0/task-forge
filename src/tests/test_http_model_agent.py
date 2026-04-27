from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.apps.worker.http_model_agent import run_model_agent_if_enabled
from src.apps.worker.types import WorkerContext
from src.packages.core.db.models import TaskORM


def _make_config_path() -> Path:
    workspace_tmp = ROOT / ".workplace" / "pytest-http-model"
    workspace_tmp.mkdir(parents=True, exist_ok=True)
    return workspace_tmp / f"model-config-{uuid.uuid4().hex}.json"


def test_http_model_agent_uses_json_config_and_parses_response(monkeypatch) -> None:
    config_path = _make_config_path()
    try:
        config_path.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "request": {
                        "format": "openai_chat_completions",
                        "url": "https://example.test/v1/chat/completions",
                        "timeout_seconds": 30,
                        "headers": {
                            "Authorization": "Bearer ${TEST_HTTP_MODEL_KEY}",
                            "Content-Type": "application/json",
                        },
                    },
                    "defaults": {
                        "model": "test-model",
                        "temperature": 0.1,
                        "max_tokens": 512,
                    },
                    "agents": {
                        "search_agent": {
                            "system_prompt": "Search prompt",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("TASK_FORGE_MODEL_CONFIG", str(config_path))
        monkeypatch.setenv("TEST_HTTP_MODEL_KEY", "secret-key")

        task = TaskORM(
            id="task-1",
            title="Search task",
            task_type="research_topic",
            input_payload={"query": "httpx json parsing"},
            expected_output_schema={"type": "object"},
        )
        context = WorkerContext(
            run_id="run-1",
            task_id="task-1",
            agent_role_name="search_agent",
            started_at=datetime.now(timezone.utc),
            cancellation_check=lambda: False,
        )

        response = Mock()
        response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "status": "ok",
                                "summary": "researched topic",
                                "result": {"findings": ["httpx"]},
                                "warnings": [],
                                "next_action_hint": "draft answer",
                            }
                        )
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 17,
                "total_tokens": 28,
            },
        }
        response.raise_for_status.return_value = None

        with patch("src.apps.worker.http_model_agent.httpx.post", return_value=response) as mocked_post:
            payload = run_model_agent_if_enabled("search_agent", task, context)

        assert payload is not None
        assert payload["status"] == "ok"
        assert payload["stage"] == "search"
        assert payload["result"]["stage"] == "search"
        assert payload["result"]["task_id"] == "task-1"
        assert payload["_execution_meta"]["token_usage"]["total_tokens"] == 28

        _, kwargs = mocked_post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer secret-key"
        assert kwargs["json"]["model"] == "test-model"
        assert kwargs["json"]["messages"][0]["content"] == "Search prompt"
    finally:
        config_path.unlink(missing_ok=True)


def test_http_model_agent_returns_none_when_disabled(monkeypatch) -> None:
    config_path = _make_config_path()
    try:
        config_path.write_text(json.dumps({"enabled": False}), encoding="utf-8")
        monkeypatch.setenv("TASK_FORGE_MODEL_CONFIG", str(config_path))

        task = TaskORM(
            id="task-2",
            title="No-op task",
            task_type="research_topic",
            input_payload={"query": "disabled"},
            expected_output_schema={"type": "object"},
        )
        context = WorkerContext(
            run_id="run-2",
            task_id="task-2",
            agent_role_name="search_agent",
            started_at=datetime.now(timezone.utc),
            cancellation_check=lambda: False,
        )

        payload = run_model_agent_if_enabled("search_agent", task, context)

        assert payload is None
    finally:
        config_path.unlink(missing_ok=True)


def test_http_model_agent_parse_error_includes_raw_response_preview(monkeypatch) -> None:
    config_path = _make_config_path()
    try:
        config_path.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "request": {
                        "format": "openai_chat_completions",
                        "url": "https://example.test/v1/chat/completions",
                        "timeout_seconds": 30,
                    },
                    "defaults": {
                        "model": "test-model",
                        "temperature": 0.1,
                        "max_tokens": 512,
                    },
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("TASK_FORGE_MODEL_CONFIG", str(config_path))

        task = TaskORM(
            id="task-3",
            title="Bad JSON task",
            task_type="research_topic",
            input_payload={"query": "bad json"},
            expected_output_schema={"type": "object"},
        )
        context = WorkerContext(
            run_id="run-3",
            task_id="task-3",
            agent_role_name="search_agent",
            started_at=datetime.now(timezone.utc),
            cancellation_check=lambda: False,
        )

        response = Mock()
        response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"status": "ok", "summary": "broken" "result": {}}'
                    }
                }
            ]
        }
        response.raise_for_status.return_value = None

        with patch("src.apps.worker.http_model_agent.httpx.post", return_value=response):
            try:
                run_model_agent_if_enabled("search_agent", task, context)
            except ValueError as exc:
                message = str(exc)
            else:
                raise AssertionError("Expected ValueError for broken JSON")

        assert "raw_response_preview=" in message
        assert "broken" in message
    finally:
        config_path.unlink(missing_ok=True)
