from __future__ import annotations

from typing import Any

import pytest

from src.apps.api import intent_recognition
from src.packages.core.intent import rule_based_intent
from src.packages.core.task_batch_normalization import normalize_batch_tasks


def test_rule_based_intent_keeps_coding_when_markdown_is_requested() -> None:
    intent = rule_based_intent(
        {
            "title": "Submitted task",
            "description": "写一个判断字符串是否为空的 Go 代码，以 markdown 给我",
            "task_type": "auto",
            "input_payload": {"prompt": "写一个判断字符串是否为空的 Go 代码，以 markdown 给我"},
        }
    )

    assert intent.primary_intent == "coding"
    assert intent.task_type == "code"
    assert intent.language == "go"
    assert intent.deliverable_contract.expected_artifact_types == ["document"]
    assert intent.deliverable_contract.deliverable_type == "markdown"
    assert intent.deliverable_contract.presentation_format == "markdown"
    assert intent.deliverable_contract.include_code_block is True


def test_rule_based_intent_classifies_essay_as_writing_document() -> None:
    intent = rule_based_intent(
        {
            "title": "Submitted task",
            "description": "写一篇800字的作文。作文内容是：我的母亲",
            "task_type": "auto",
            "input_payload": {"prompt": "写一篇800字的作文。作文内容是：我的母亲"},
        }
    )

    assert intent.primary_intent == "writing"
    assert intent.task_type == "worker_execute"
    assert intent.deliverable_contract.expected_artifact_types == ["document"]
    assert intent.deliverable_contract.deliverable_type == "markdown"
    assert intent.deliverable_contract.presentation_format == "markdown"
    assert intent.deliverable_contract.file_extension == ".md"


def test_rule_based_intent_recognizes_txt_deliverable() -> None:
    intent = rule_based_intent(
        {
            "title": "Submitted task",
            "description": "Write a short plain text summary and save it as a .txt file",
            "task_type": "auto",
            "input_payload": {"prompt": "Write a short plain text summary and save it as a .txt file"},
        }
    )

    assert intent.deliverable_contract.expected_artifact_types == ["document"]
    assert intent.deliverable_contract.deliverable_type == "txt"
    assert intent.deliverable_contract.presentation_format == "plain_text"
    assert intent.deliverable_contract.file_extension == ".txt"


def test_normalization_writes_recognized_intent_and_contract_for_auto_task() -> None:
    normalized, items = normalize_batch_tasks(
        [
            {
                "client_task_id": "task_1",
                "title": "Submitted task",
                "description": "写一个 Python 代码，以 markdown 给我",
                "task_type": "auto",
                "input_payload": {"prompt": "写一个 Python 代码，以 markdown 给我"},
            }
        ],
        intent_recognizer=lambda task: rule_based_intent(task, provided_task_type=task.get("task_type")),
    )

    assert normalized[0]["task_type"] == "code"
    assert normalized[0]["input_payload"]["intent"]["primary_intent"] == "coding"
    assert normalized[0]["input_payload"]["deliverable_contract"]["expected_artifact_types"] == ["document"]
    assert normalized[0]["input_payload"]["deliverable_contract"]["deliverable_type"] == "markdown"
    assert items[0].recognized_intent is not None
    assert items[0].recognized_intent["task_type"] == "code"


def test_model_intent_service_parses_openai_compatible_response(monkeypatch: pytest.MonkeyPatch) -> None:
    intent_recognition._CACHE.clear()

    monkeypatch.setattr(
        intent_recognition,
        "_intent_model_config",
        lambda: {
            "enabled": True,
            "request_format": "openai_chat_completions",
            "url": "http://model.local/v1/chat/completions",
            "headers": {},
            "model": "intent-test",
            "temperature": 0,
            "max_tokens": 800,
            "timeout_seconds": 1,
            "extra_body": {},
        },
    )

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"primary_intent":"coding","task_type":"code","confidence":0.92,'
                            '"language":"go","subject":"Go code","operation":"generate",'
                            '"deliverable_contract":{"expected_artifact_types":["document"],'
                            '"presentation_format":"markdown","file_extension":".md",'
                            '"include_code_block":true,"require_file_level_artifact":false,'
                            '"allow_primary_json_only":false},'
                            '"routing_hints":{"preferred_agent_roles":["code_agent"],'
                            '"required_capabilities":["task:code"],"avoid_agent_roles":[]},'
                            '"warnings":[]}'
                        }
                    }
                ]
            }

    monkeypatch.setattr(intent_recognition.httpx, "post", lambda *_, **__: Response())

    intent = intent_recognition.recognize_intent_for_task(
        {
            "title": "Submitted task",
            "description": "写 Go 代码，以 markdown 给我",
            "task_type": "auto",
            "input_payload": {"prompt": "写 Go 代码，以 markdown 给我"},
        }
    )

    assert intent.source == "model"
    assert intent.task_type == "code"
    assert intent.language == "go"
    assert intent.deliverable_contract.expected_artifact_types == ["document"]
    assert intent.deliverable_contract.deliverable_type == "markdown"


def test_model_intent_service_uses_global_model_config_without_role_override(monkeypatch: pytest.MonkeyPatch) -> None:
    intent_recognition._CACHE.clear()
    monkeypatch.setattr(
        intent_recognition,
        "load_model_config",
        lambda: {
            "enabled": True,
            "request": {"url": "http://model.local/v1/chat/completions"},
            "defaults": {"model": "intent-test"},
            "agents": {},
        },
    )
    monkeypatch.setattr(
        intent_recognition,
        "resolve_model_request_config",
        lambda role_name: {
            "enabled": True,
            "request_format": "openai_chat_completions",
            "url": "http://model.local/v1/chat/completions",
            "headers": {},
            "model": "intent-test",
            "temperature": 0,
            "max_tokens": 800,
            "timeout_seconds": 1,
            "extra_body": {},
        },
    )

    calls = {"count": 0}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"primary_intent":"writing","task_type":"worker_execute","confidence":0.91,'
                            '"language":null,"subject":"作文","operation":"generate",'
                            '"deliverable_contract":{"expected_artifact_types":["document"],'
                            '"presentation_format":"markdown","file_extension":".md",'
                            '"include_code_block":false,"require_file_level_artifact":false,'
                            '"allow_primary_json_only":false},'
                            '"routing_hints":{"preferred_agent_roles":["worker_agent"],'
                            '"required_capabilities":["task:worker_execute"],"avoid_agent_roles":[]},'
                            '"warnings":[]}'
                        }
                    }
                ]
            }

    def post(*_: Any, **__: Any) -> Response:
        calls["count"] += 1
        return Response()

    monkeypatch.setattr(intent_recognition.httpx, "post", post)

    intent = intent_recognition.recognize_intent_for_task(
        {
            "title": "Submitted task",
            "description": "写一篇800字的作文。作文内容是：我的母亲",
            "task_type": "auto",
            "input_payload": {"prompt": "写一篇800字的作文。作文内容是：我的母亲"},
        }
    )

    assert calls["count"] == 1
    assert intent.source == "model"
    assert intent.primary_intent == "writing"
    assert intent.deliverable_contract.expected_artifact_types == ["document"]
    assert intent.deliverable_contract.deliverable_type == "markdown"


def test_model_intent_service_falls_back_to_rules_on_model_error(monkeypatch: pytest.MonkeyPatch) -> None:
    intent_recognition._CACHE.clear()
    monkeypatch.setattr(
        intent_recognition,
        "_intent_model_config",
        lambda: {
            "enabled": True,
            "request_format": "openai_chat_completions",
            "url": "http://model.local/v1/chat/completions",
            "headers": {},
            "model": "intent-test",
            "temperature": 0,
            "max_tokens": 800,
            "timeout_seconds": 1,
            "extra_body": {},
        },
    )
    monkeypatch.setattr(intent_recognition.httpx, "post", lambda *_, **__: (_ for _ in ()).throw(RuntimeError("boom")))

    intent = intent_recognition.recognize_intent_for_task(
        {
            "title": "Submitted task",
            "description": "写一个 Python 代码",
            "task_type": "auto",
            "input_payload": {"prompt": "写一个 Python 代码"},
        }
    )

    assert intent.source == "rules_fallback"
    assert intent.task_type == "code"
    assert any("intent_model_fallback" in warning for warning in intent.warnings)
