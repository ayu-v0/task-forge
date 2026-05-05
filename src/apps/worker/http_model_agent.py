from __future__ import annotations

import json
import re
from typing import Any

import httpx

from src.apps.worker.model_config import resolve_model_request_config
from src.apps.worker.types import WorkerContext
from src.packages.core.db.models import TaskORM


ROLE_STAGE_NAMES = {
    "default_worker": "worker",
    "echo_worker": "echo",
    "search_agent": "search",
    "code_agent": "code",
    "planner_agent": "planner",
    "worker_agent": "worker",
    "reviewer_agent": "reviewer",
}

ROLE_SYSTEM_PROMPTS = {
    "default_worker": (
        "You are a default execution agent. Return concise structured JSON. "
        "Use the result object to summarize the task input, execution outcome, and any useful outputs."
    ),
    "echo_worker": (
        "You are an echo/debug agent. Return concise structured JSON. "
        "Use the result object to reflect the task input and execution context for debugging."
    ),
    "search_agent": (
        "You are a research agent. Return concise structured JSON. "
        "Use the result object to provide findings, keywords, search plan, and source suggestions."
    ),
    "code_agent": (
        "You are a code implementation agent. Return concise structured JSON. "
        "When the task asks for code, deliver usable source code as result.deliverables code_file items. "
        "Use implementation steps and risks only as supporting context, not as the primary deliverable."
    ),
    "planner_agent": (
        "You are a planning agent. Return concise structured JSON. "
        "Use the result object to break the request into clear ordered steps."
    ),
    "worker_agent": (
        "You are an execution agent. Return concise structured JSON. "
        "Use the result object to summarize what was completed and any outputs produced."
    ),
    "reviewer_agent": (
        "You are a reviewer agent. Return concise structured JSON. "
        "Set status to 'needs_review' when the input is insufficient or should be manually checked."
    ),
}

REQUIRED_FIELDS = ("status", "summary", "result")
RAW_RESPONSE_PREVIEW_CHARS = 800


def _preview_text(value: str, limit: int = RAW_RESPONSE_PREVIEW_CHARS) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}..."


def _serialize_context(context: WorkerContext) -> dict[str, Any]:
    return {
        "run_id": context.run_id,
        "task_id": context.task_id,
        "agent_role_name": context.agent_role_name,
        "started_at": context.started_at.isoformat(),
    }


def _extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                text_value = item.get("text")
                if isinstance(text_value, str):
                    parts.append(text_value)
        return "\n".join(part for part in parts if part)
    raise ValueError("Unsupported model response content")


def _extract_json_block(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            stripped = "\n".join(lines[1:-1]).strip()

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("Model response does not contain a JSON object")
    return stripped[start : end + 1]


def _deliverable_contract(task: TaskORM) -> dict[str, Any]:
    payload = task.input_payload if isinstance(task.input_payload, dict) else {}
    contract = payload.get("deliverable_contract")
    return contract if isinstance(contract, dict) else {}


def _expected_artifact_types(task: TaskORM) -> set[str]:
    expected = _deliverable_contract(task).get("expected_artifact_types")
    if not isinstance(expected, list):
        return set()
    return {str(item).strip() for item in expected if str(item).strip()}


def _language_extension(language: str) -> str:
    return {
        "python": ".py",
        "py": ".py",
        "go": ".go",
        "golang": ".go",
        "javascript": ".js",
        "js": ".js",
        "typescript": ".ts",
        "ts": ".ts",
        "powershell": ".ps1",
        "shell": ".sh",
        "bash": ".sh",
        "markdown": ".md",
    }.get(language.strip().lower(), ".txt")


def _extract_fenced_code(text: str) -> tuple[str, str] | None:
    match = re.search(r"```([a-zA-Z0-9_+-]*)\s*\n(.*?)```", text, flags=re.DOTALL)
    if not match:
        return None
    language = match.group(1).strip().lower()
    code = match.group(2).strip()
    if not code:
        return None
    return language, code


def _extract_content_field(text: str) -> str | None:
    match = re.search(r'"content"\s*:\s*"((?:\\.|[^"\\])*)"', text, flags=re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(f'"{match.group(1)}"')
    except json.JSONDecodeError:
        return match.group(1).replace("\\n", "\n").replace('\\"', '"')


def _wrap_non_json_model_output(
    *,
    role_name: str,
    task: TaskORM,
    context: WorkerContext,
    content_text: str,
    parse_error: Exception,
    token_usage: dict[str, int] | None,
) -> dict[str, Any]:
    raw_content = (_extract_content_field(content_text) or content_text).strip()
    expected_types = _expected_artifact_types(task)
    payload = task.input_payload if isinstance(task.input_payload, dict) else {}
    language = str(payload.get("language") or "").strip().lower()
    warnings = [
        "model_output_wrapped_from_non_json",
        f"model_output_parse_error: {parse_error}",
    ]
    result: dict[str, Any] = {"content": raw_content}

    if "document" in expected_types:
        result["deliverables"] = [
            {
                "type": "document",
                "path": f"generated/{task.id}.md",
                "title": task.title,
                "language": "markdown",
                "content": raw_content,
            }
        ]
    elif "code_file" in expected_types:
        fenced = _extract_fenced_code(content_text)
        if fenced is not None:
            fence_language, code = fenced
            output_language = fence_language or language or "text"
            result["deliverables"] = [
                {
                    "type": "code_file",
                    "path": f"generated/{task.id}{_language_extension(output_language)}",
                    "language": output_language,
                    "change_type": "created",
                    "content": code,
                }
            ]
        else:
            warnings.append("code_file_contract_without_detectable_code_block")
            result["deliverables"] = [
                {
                    "type": "document",
                    "path": f"generated/{task.id}.md",
                    "title": task.title,
                    "language": "markdown",
                    "content": raw_content,
                }
            ]
    elif "code_patch" in expected_types:
        warnings.append("code_patch_contract_without_diff")

    return _normalize_payload(
        role_name,
        task,
        context,
        {
            "status": "success",
            "summary": "Model returned non-JSON content; wrapped as deliverable content.",
            "result": result,
            "warnings": warnings,
            "next_action_hint": None,
        },
        token_usage=token_usage,
    )


def _normalize_payload(
    role_name: str,
    task: TaskORM,
    context: WorkerContext,
    payload: dict[str, Any],
    *,
    token_usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    missing_fields = [field for field in REQUIRED_FIELDS if field not in payload]
    if missing_fields:
        raise ValueError(f"Model response missing required fields: {', '.join(missing_fields)}")

    stage_name = str(payload.get("stage") or ROLE_STAGE_NAMES.get(role_name, role_name))
    result = payload.get("result")
    if not isinstance(result, dict):
        result = {"value": result}

    result.setdefault("stage", stage_name)
    result.setdefault("task_id", task.id)
    result.setdefault("context", _serialize_context(context))

    warnings = payload.get("warnings", [])
    if not isinstance(warnings, list):
        warnings = [str(warnings)] if warnings else []

    normalized = {
        "status": str(payload.get("status")),
        "summary": str(payload.get("summary")),
        "result": result,
        "warnings": [str(item) for item in warnings],
        "next_action_hint": (
            None if payload.get("next_action_hint") is None else str(payload.get("next_action_hint"))
        ),
        "stage": stage_name,
        "task_id": task.id,
        "context": result["context"],
    }

    for key, value in payload.items():
        if key not in normalized and key != "_execution_meta":
            normalized[key] = value

    if token_usage:
        normalized["_execution_meta"] = {"token_usage": token_usage}

    return normalized


def _build_request_body(
    role_name: str,
    task: TaskORM,
    context: WorkerContext,
    config: dict[str, Any],
) -> dict[str, Any]:
    system_prompt = config.get("system_prompt") or ROLE_SYSTEM_PROMPTS.get(role_name, "")
    instruction = (
        "Return exactly one JSON object with keys "
        "'status', 'summary', 'result', 'warnings', and 'next_action_hint'. "
        "The result object should include the useful structured output for this role. "
        "If you produce code, include result.deliverables with a code_file item containing "
        "type, path, language, change_type, and content. "
        "Follow task.input_payload.deliverable_contract exactly when it is present. "
        "If expected_artifact_types contains document and presentation_format is markdown, "
        "return result.deliverables with a document item containing markdown content. "
        "If include_code_block is true, include fenced code blocks in that document. "
        "If expected_artifact_types contains code_file, return code_file items. "
        "If expected_artifact_types contains code_patch, return code_patch items. "
        "Do not return only summary when a deliverable contract exists."
    )
    request_payload = {
        "role_name": role_name,
        "task": {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "task_type": task.task_type,
            "input_payload": task.input_payload,
            "expected_output_schema": task.expected_output_schema,
        },
        "context": _serialize_context(context),
        "instructions": instruction,
    }

    body = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(request_payload, ensure_ascii=False),
            },
        ],
        "temperature": config["temperature"],
        "max_tokens": config["max_tokens"],
    }
    body.update(config.get("extra_body") or {})
    return body


def _call_openai_compatible_model(
    role_name: str,
    task: TaskORM,
    context: WorkerContext,
    config: dict[str, Any],
) -> dict[str, Any]:
    if not config.get("url"):
        raise RuntimeError(f"Model config for role={role_name} is missing request.url")
    if not config.get("model"):
        raise RuntimeError(f"Model config for role={role_name} is missing defaults.model")

    body = _build_request_body(role_name, task, context, config)
    response = httpx.post(
        config["url"],
        headers=config.get("headers") or {},
        json=body,
        timeout=config["timeout_seconds"],
    )
    response.raise_for_status()
    payload = response.json()
    choices = payload.get("choices") or []
    if not choices:
        raise ValueError("Model response missing choices")

    usage = payload.get("usage") or {}
    token_usage = {
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
    }
    message = choices[0].get("message") or {}
    content_text = _extract_message_text(message.get("content"))
    try:
        json_text = _extract_json_block(content_text)
        structured_payload = json.loads(json_text)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        if not content_text.strip():
            raise ValueError(
                "Model response could not be parsed as JSON: "
                f"{exc}; raw_response_preview={_preview_text(content_text)}"
            ) from exc
        return _wrap_non_json_model_output(
            role_name=role_name,
            task=task,
            context=context,
            content_text=content_text,
            parse_error=exc,
            token_usage=token_usage,
        )
    return _normalize_payload(
        role_name,
        task,
        context,
        structured_payload,
        token_usage=token_usage,
    )


def run_model_agent_if_enabled(
    role_name: str,
    task: TaskORM,
    context: WorkerContext,
) -> dict[str, Any] | None:
    config = resolve_model_request_config(role_name)
    if not config.get("enabled"):
        return None
    if config.get("request_format") != "openai_chat_completions":
        raise RuntimeError(
            f"Unsupported request format for role={role_name}: {config.get('request_format')}"
        )
    return _call_openai_compatible_model(role_name, task, context, config)
