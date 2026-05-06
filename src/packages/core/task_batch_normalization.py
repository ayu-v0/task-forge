from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable

from src.packages.core.intent import TaskIntent, is_auto_task_type


STAGE_DEPENDENCY_HINTS = {
    "write_summary": {"research", "analyze", "analysis"},
    "implement": {"design", "spec", "plan"},
    "test": {"implement", "build", "code"},
    "review": {"test", "draft", "write", "implement"},
}

AMBIGUOUS_TITLE_MARKERS = {"处理一下", "优化", "修复", "看看", "任务"}
AMBIGUOUS_TASK_TYPES = {"", "unknown", "task", "misc", "general"}


@dataclass
class NormalizedTask:
    source_client_task_id: str
    effective_client_task_id: str
    title: str
    description: str | None
    task_type: str
    priority: str
    input_payload: dict[str, Any]
    expected_output_schema: dict[str, Any]
    dependency_client_task_ids: list[str]
    is_ambiguous: bool
    missing_fields_filled: list[str]
    inferred_dependency_client_task_ids: list[str]
    action: str
    notes: list[str]
    recognized_intent: dict[str, Any] | None = None


def _normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _normalized_title(value: str) -> str:
    return _normalize_spaces(value).lower()


def _string_length_score(value: str | None) -> int:
    return len((value or "").strip())


def _task_completeness_score(task: dict[str, Any]) -> tuple[int, int, int]:
    description_score = _string_length_score(task.get("description"))
    payload_score = len(task.get("input_payload") or {})
    schema_score = len(task.get("expected_output_schema") or {})
    return (description_score, payload_score, schema_score)


def _dependency_signature(values: list[str] | None) -> tuple[str, ...]:
    return tuple(values or [])


def _exact_signature(task: dict[str, Any]) -> str:
    return json.dumps(
        {
            "title": _normalize_spaces(task["title"]),
            "task_type": task["task_type"],
            "description": _normalize_spaces(task.get("description") or ""),
            "input_payload": task.get("input_payload") or {},
            "expected_output_schema": task.get("expected_output_schema") or {},
            "dependency_client_task_ids": task.get("dependency_client_task_ids") or [],
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def _merge_signature(task: dict[str, Any]) -> tuple[str, str]:
    return (task["task_type"], _normalized_title(task["title"]))


def _should_replace(current_best: dict[str, Any], challenger: dict[str, Any]) -> bool:
    return _task_completeness_score(challenger) > _task_completeness_score(current_best)


def _fill_defaults(task: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    filled = dict(task)
    missing_fields_filled: list[str] = []

    description = filled.get("description")
    if not description or not str(description).strip():
        filled["description"] = filled["title"]
        missing_fields_filled.append("description")

    expected_output_schema = filled.get("expected_output_schema")
    if not expected_output_schema:
        filled["expected_output_schema"] = {"type": "object"}
        missing_fields_filled.append("expected_output_schema")

    priority = filled.get("priority")
    if not priority:
        filled["priority"] = "medium"
        missing_fields_filled.append("priority")

    dependencies = filled.get("dependency_client_task_ids")
    if dependencies is None:
        filled["dependency_client_task_ids"] = []
        missing_fields_filled.append("dependency_client_task_ids")

    input_payload = filled.get("input_payload")
    if input_payload is None:
        filled["input_payload"] = {}
        missing_fields_filled.append("input_payload")

    return filled, missing_fields_filled


def _intent_payload(intent: TaskIntent) -> dict[str, Any]:
    data = intent.model_dump()
    return {
        key: value
        for key, value in data.items()
        if key not in {"deliverable_contract", "routing_hints"}
    }


def _apply_intent(
    task: dict[str, Any],
    intent: TaskIntent | None,
) -> tuple[dict[str, Any], dict[str, Any] | None, list[str]]:
    if intent is None:
        return task, None, []

    filled = dict(task)
    payload = dict(filled.get("input_payload") or {})
    intent_data = intent.model_dump()
    original_task_type = str(filled.get("task_type") or "").strip().lower()
    notes = [
        f"intent recognized source={intent.source} primary_intent={intent.primary_intent} task_type={intent.task_type}",
    ]

    if is_auto_task_type(original_task_type):
        filled["task_type"] = intent.task_type
        notes.append(f"task_type normalized from {original_task_type or 'empty'} to {intent.task_type}")
    elif original_task_type != intent.task_type:
        notes.append(f"explicit task_type preserved despite recognized task_type={intent.task_type}")

    payload["intent"] = _intent_payload(intent)
    payload["deliverable_contract"] = intent.deliverable_contract.model_dump()
    payload["routing_hints"] = intent.routing_hints.model_dump()
    if intent.language and not payload.get("language"):
        payload["language"] = intent.language
    filled["input_payload"] = payload

    for warning in intent.warnings:
        notes.append(f"intent warning: {warning}")
    return filled, intent_data, notes


def _is_ambiguous(task: dict[str, Any]) -> bool:
    title = _normalize_spaces(task["title"])
    if len(title) < 4:
        return True
    lowered_title = title.lower()
    if any(marker in title for marker in AMBIGUOUS_TITLE_MARKERS):
        return True
    if (task.get("task_type") or "").strip().lower() in AMBIGUOUS_TASK_TYPES:
        return True
    payload = task.get("input_payload") or {}
    if not payload:
        return True
    if set(payload.keys()) <= {"text", "content"} and not any(str(value).strip() for value in payload.values()):
        return True
    return False


def _infer_dependency(
    task: dict[str, Any],
    previous_tasks: list[dict[str, Any]],
) -> list[str]:
    existing = list(task.get("dependency_client_task_ids") or [])
    if existing:
        return []

    task_type = (task.get("task_type") or "").strip().lower()
    title = _normalized_title(task["title"])
    hints = STAGE_DEPENDENCY_HINTS.get(task_type, set())
    if not hints:
        if "review" in title:
            hints = {"draft", "write", "implement"}
        elif "test" in title:
            hints = {"implement", "build", "code"}
        elif "implement" in title:
            hints = {"design", "plan", "spec"}

    for previous in reversed(previous_tasks):
        previous_type = (previous.get("task_type") or "").strip().lower()
        previous_title = _normalized_title(previous["title"])
        if previous_type in hints or any(hint in previous_title for hint in hints):
            return [previous["client_task_id"]]
    return []


def normalize_batch_tasks(
    tasks: list[dict[str, Any]],
    *,
    intent_recognizer: Callable[[dict[str, Any]], TaskIntent] | None = None,
) -> tuple[list[dict[str, Any]], list[NormalizedTask]]:
    exact_seen: dict[str, dict[str, Any]] = {}
    merge_seen: dict[tuple[str, str], dict[str, Any]] = {}
    normalization_items: list[NormalizedTask] = []
    normalized_tasks: list[dict[str, Any]] = []

    for original_task in tasks:
        filled_task, missing_fields_filled = _fill_defaults(original_task)
        recognized_intent: dict[str, Any] | None = None
        intent_notes: list[str] = []
        if intent_recognizer is not None:
            intent = intent_recognizer(filled_task)
            filled_task, recognized_intent, intent_notes = _apply_intent(filled_task, intent)
        exact_key = _exact_signature(filled_task)

        if exact_key in exact_seen:
            kept = exact_seen[exact_key]
            normalization_items.append(
                NormalizedTask(
                    source_client_task_id=original_task["client_task_id"],
                    effective_client_task_id=kept["client_task_id"],
                    title=filled_task["title"],
                    description=filled_task["description"],
                    task_type=filled_task["task_type"],
                    priority=filled_task["priority"],
                    input_payload=filled_task["input_payload"],
                    expected_output_schema=filled_task["expected_output_schema"],
                    dependency_client_task_ids=filled_task["dependency_client_task_ids"],
                    is_ambiguous=_is_ambiguous(filled_task),
                    missing_fields_filled=missing_fields_filled,
                    inferred_dependency_client_task_ids=[],
                    action="deduped",
                    notes=[f"deduped_into={kept['client_task_id']}"],
                    recognized_intent=recognized_intent,
                )
            )
            continue

        merge_key = _merge_signature(filled_task)
        if merge_key in merge_seen:
            kept = merge_seen[merge_key]
            if _dependency_signature(kept.get("dependency_client_task_ids")) == _dependency_signature(
                filled_task.get("dependency_client_task_ids")
            ):
                if _should_replace(kept, filled_task):
                    kept.update(
                        {
                            "title": filled_task["title"],
                            "description": filled_task["description"],
                            "priority": filled_task["priority"],
                            "input_payload": filled_task["input_payload"],
                            "expected_output_schema": filled_task["expected_output_schema"],
                            "task_type": filled_task["task_type"],
                        }
                    )
                normalization_items.append(
                    NormalizedTask(
                        source_client_task_id=original_task["client_task_id"],
                        effective_client_task_id=kept["client_task_id"],
                        title=filled_task["title"],
                        description=filled_task["description"],
                        task_type=filled_task["task_type"],
                        priority=filled_task["priority"],
                        input_payload=filled_task["input_payload"],
                        expected_output_schema=filled_task["expected_output_schema"],
                        dependency_client_task_ids=filled_task["dependency_client_task_ids"],
                        is_ambiguous=_is_ambiguous(filled_task),
                        missing_fields_filled=missing_fields_filled,
                        inferred_dependency_client_task_ids=[],
                        action="merged",
                        notes=[f"merged_into={kept['client_task_id']}"],
                        recognized_intent=recognized_intent,
                    )
                )
                continue

        inferred_dependency_client_task_ids = _infer_dependency(filled_task, normalized_tasks)
        if inferred_dependency_client_task_ids:
            filled_task["dependency_client_task_ids"] = inferred_dependency_client_task_ids

        normalized_tasks.append(filled_task)
        exact_seen[_exact_signature(filled_task)] = filled_task
        merge_seen[merge_key] = filled_task
        notes: list[str] = [*intent_notes]
        action = "normalized" if missing_fields_filled or inferred_dependency_client_task_ids else "kept"
        if intent_notes and action == "kept":
            action = "normalized"
        if _is_ambiguous(filled_task):
            notes.append("task marked as ambiguous")
            if action == "kept":
                action = "normalized"
        if inferred_dependency_client_task_ids:
            notes.append("dependency inferred from stage ordering")
        if missing_fields_filled:
            notes.append("missing fields filled with defaults")

        normalization_items.append(
            NormalizedTask(
                source_client_task_id=original_task["client_task_id"],
                effective_client_task_id=filled_task["client_task_id"],
                title=filled_task["title"],
                description=filled_task["description"],
                task_type=filled_task["task_type"],
                priority=filled_task["priority"],
                input_payload=filled_task["input_payload"],
                expected_output_schema=filled_task["expected_output_schema"],
                dependency_client_task_ids=filled_task["dependency_client_task_ids"],
                is_ambiguous=_is_ambiguous(filled_task),
                missing_fields_filled=missing_fields_filled,
                inferred_dependency_client_task_ids=inferred_dependency_client_task_ids,
                action=action,
                notes=notes,
                recognized_intent=recognized_intent,
            )
        )

    return normalized_tasks, normalization_items
