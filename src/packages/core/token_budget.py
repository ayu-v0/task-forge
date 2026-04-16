from __future__ import annotations

import json
from copy import deepcopy
from math import ceil
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.packages.core.db.models import AgentRoleORM, ExecutionRunORM, TaskORM
from src.packages.core.schemas import PromptBudgetPolicyRead


JSON_SEPARATORS = (",", ":")
HISTORY_KEYS = {"history", "messages", "conversation", "previous_runs", "history_background"}
DEPENDENCY_KEYS = {"dependencies", "dependency_details", "upstream_outputs", "dependency_context"}
LONG_STRING_LIMIT = 512
SUMMARY_STRING_LIMIT = 160
MIN_GLOBAL_BACKGROUND_TOKENS = 32


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=JSON_SEPARATORS)


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, ceil(len(text) / 4))


def _load_policy(agent_role: AgentRoleORM) -> PromptBudgetPolicyRead:
    return PromptBudgetPolicyRead.model_validate(agent_role.input_schema.get("prompt_budget_policy") or {})


def _global_background_source(task: TaskORM, agent_role: AgentRoleORM) -> str:
    return _stable_json(
        {
            "role_name": agent_role.role_name,
            "description": agent_role.description or "",
            "capabilities": agent_role.capabilities or [],
            "task": {
                "title": task.title,
                "description": task.description or "",
                "task_type": task.task_type,
                "priority": task.priority,
            },
        }
    )


def _dependency_records(db: Session, task: TaskORM) -> list[dict[str, Any]]:
    if not task.dependency_ids:
        return []

    dependency_tasks = db.scalars(
        select(TaskORM)
        .where(TaskORM.id.in_(task.dependency_ids))
        .order_by(TaskORM.created_at.asc(), TaskORM.id.asc())
    ).all()

    records: list[dict[str, Any]] = []
    for dependency in dependency_tasks:
        latest_run = db.scalars(
            select(ExecutionRunORM)
            .where(ExecutionRunORM.task_id == dependency.id)
            .order_by(ExecutionRunORM.started_at.desc(), ExecutionRunORM.id.desc())
        ).first()
        records.append(
            {
                "task_id": dependency.id,
                "title": dependency.title,
                "status": dependency.status,
                "assigned_agent_role": dependency.assigned_agent_role,
                "latest_output": latest_run.output_snapshot if latest_run is not None else {},
                "latest_output_keys": sorted((latest_run.output_snapshot or {}).keys()) if latest_run is not None else [],
                "latest_run_status": latest_run.run_status if latest_run is not None else None,
            }
        )
    return records


def _dependency_summary_source(db: Session, task: TaskORM) -> str:
    records = _dependency_records(db, task)
    if not records:
        return ""
    summary = [
        {
            "task_id": item["task_id"],
            "title": item["title"],
            "status": item["status"],
            "assigned_agent_role": item["assigned_agent_role"],
            "latest_output_keys": item["latest_output_keys"],
            "latest_run_status": item["latest_run_status"],
        }
        for item in records
    ]
    return _stable_json(summary)


def _result_summary_source(db: Session, task: TaskORM) -> str:
    records = _dependency_records(db, task)
    if not records:
        return ""
    summary = [
        {
            "task_id": item["task_id"],
            "latest_output": item["latest_output"],
        }
        for item in records
        if item["latest_output"]
    ]
    return _stable_json(summary)


def _validation_rules_source(task: TaskORM, agent_role: AgentRoleORM) -> str:
    return _stable_json(
        {
            "expected_output_schema": task.expected_output_schema or {},
            "input_schema": agent_role.input_schema or {},
            "output_schema": agent_role.output_schema or {},
        }
    )


def _history_background_source(db: Session, task: TaskORM) -> str:
    previous_runs = db.scalars(
        select(ExecutionRunORM)
        .where(ExecutionRunORM.task_id == task.id)
        .order_by(ExecutionRunORM.started_at.desc(), ExecutionRunORM.id.desc())
        .limit(5)
    ).all()
    if not previous_runs:
        return ""
    summary = [
        {
            "run_status": run.run_status,
            "error_message": run.error_message,
            "latency_ms": run.latency_ms,
            "output_keys": sorted((run.output_snapshot or {}).keys()),
        }
        for run in previous_runs
    ]
    return _stable_json(summary)


def _capped_tokens(raw_text: str, max_tokens: int) -> int:
    if max_tokens <= 0:
        return 0
    return min(estimate_text_tokens(raw_text), max_tokens)


def _build_summary(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _build_summary(item) for key, item in list(value.items())[:8]}
    if isinstance(value, list):
        return [_build_summary(item) for item in value[:5]]
    if isinstance(value, str):
        if len(value) <= SUMMARY_STRING_LIMIT:
            return value
        return f"{value[:SUMMARY_STRING_LIMIT]}...[summary len={len(value)}]"
    return value


def _trim_long_strings(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _trim_long_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_trim_long_strings(item) for item in value]
    if isinstance(value, str):
        if len(value) <= LONG_STRING_LIMIT:
            return value
        return f"{value[:LONG_STRING_LIMIT]}...[trimmed len={len(value)}]"
    return value


def _remove_keys(payload: dict[str, Any], keys: set[str]) -> tuple[dict[str, Any], bool]:
    changed = False
    trimmed = {}
    for key, value in payload.items():
        if key in keys:
            changed = True
            continue
        trimmed[key] = value
    return trimmed, changed


def _summary_only_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": _build_summary(payload),
        "structured_result": {
            "keys": sorted(payload.keys()),
            "field_count": len(payload),
        },
    }


def _section_counts(
    db: Session,
    task: TaskORM,
    agent_role: AgentRoleORM,
    policy: PromptBudgetPolicyRead,
    *,
    task_input_payload: dict[str, Any],
    history_cap: int | None = None,
    dependency_cap: int | None = None,
    global_cap: int | None = None,
    task_input_cap: int | None = None,
) -> dict[str, int]:
    global_background_tokens = _capped_tokens(
        _global_background_source(task, agent_role),
        policy.max_global_background_tokens if global_cap is None else global_cap,
    )
    task_input_tokens = _capped_tokens(
        _stable_json(task_input_payload or {}),
        policy.max_task_input_tokens if task_input_cap is None else task_input_cap,
    )
    dependency_summary_tokens = _capped_tokens(
        _dependency_summary_source(db, task),
        policy.max_dependency_summary_tokens if dependency_cap is None else dependency_cap,
    )
    result_summary_tokens = _capped_tokens(
        _result_summary_source(db, task),
        policy.max_result_summary_tokens,
    )
    validation_rule_tokens = _capped_tokens(
        _validation_rules_source(task, agent_role),
        policy.max_validation_rule_tokens,
    )
    history_background_tokens = _capped_tokens(
        _history_background_source(db, task),
        policy.max_history_background_tokens if history_cap is None else history_cap,
    )
    estimated_input_tokens = (
        global_background_tokens
        + task_input_tokens
        + dependency_summary_tokens
        + result_summary_tokens
        + validation_rule_tokens
        + history_background_tokens
    )
    return {
        "global_background_tokens": global_background_tokens,
        "task_input_tokens": task_input_tokens,
        "dependency_summary_tokens": dependency_summary_tokens,
        "result_summary_tokens": result_summary_tokens,
        "validation_rule_tokens": validation_rule_tokens,
        "history_background_tokens": history_background_tokens,
        "estimated_input_tokens": estimated_input_tokens,
        "system_prompt_tokens": global_background_tokens + validation_rule_tokens,
    }


def build_execution_budget(db: Session, task: TaskORM, agent_role: AgentRoleORM) -> dict[str, Any]:
    policy = _load_policy(agent_role)
    trimmed_payload = deepcopy(task.input_payload or {})
    trim_steps: list[str] = []
    degradation_mode = "full_context"
    history_cap = policy.max_history_background_tokens
    dependency_cap = policy.max_dependency_summary_tokens
    global_cap = policy.max_global_background_tokens
    task_input_cap = policy.max_task_input_tokens

    initial_counts = _section_counts(
        db,
        task,
        agent_role,
        policy,
        task_input_payload=trimmed_payload,
        history_cap=history_cap,
        dependency_cap=dependency_cap,
        global_cap=global_cap,
        task_input_cap=task_input_cap,
    )
    initial_remaining_budget = policy.model_context_limit - initial_counts["estimated_input_tokens"] - policy.reserved_output_tokens

    current_counts = dict(initial_counts)

    def _recompute() -> int:
        nonlocal current_counts
        current_counts = _section_counts(
            db,
            task,
            agent_role,
            policy,
            task_input_payload=trimmed_payload,
            history_cap=history_cap,
            dependency_cap=dependency_cap,
            global_cap=global_cap,
            task_input_cap=task_input_cap,
        )
        return policy.model_context_limit - current_counts["estimated_input_tokens"] - policy.reserved_output_tokens

    remaining_budget = initial_remaining_budget
    if remaining_budget < 0:
        history_cap = 0
        trim_steps.append("removed_irrelevant_history")
        degradation_mode = "trimmed_history"
        remaining_budget = _recompute()

    if remaining_budget < 0:
        payload_without_dependencies, changed = _remove_keys(trimmed_payload, DEPENDENCY_KEYS)
        if changed:
            trimmed_payload = payload_without_dependencies
            trim_steps.append("removed_nonessential_dependency_payload")
        dependency_cap = 0
        trim_steps.append("removed_dependency_summary")
        degradation_mode = "trimmed_dependencies"
        remaining_budget = _recompute()

    if remaining_budget < 0:
        global_cap = min(global_cap, MIN_GLOBAL_BACKGROUND_TOKENS)
        trim_steps.append("compressed_global_background")
        degradation_mode = "compressed_global_background"
        remaining_budget = _recompute()

    if remaining_budget < 0:
        payload_without_history, changed = _remove_keys(trimmed_payload, HISTORY_KEYS)
        if changed:
            trimmed_payload = payload_without_history
            trim_steps.append("removed_history_payload")
        trimmed_payload = _trim_long_strings(trimmed_payload)
        task_input_cap = min(task_input_cap, max(128, policy.model_context_limit // 4))
        trim_steps.append("compressed_long_input")
        degradation_mode = "compressed_input"
        remaining_budget = _recompute()

    if remaining_budget < 0:
        trimmed_payload = _summary_only_payload(trimmed_payload)
        task_input_cap = min(task_input_cap, 256)
        global_cap = min(global_cap, MIN_GLOBAL_BACKGROUND_TOKENS)
        dependency_cap = 0
        history_cap = 0
        trim_steps.append("degraded_to_summary_and_structured_result")
        degradation_mode = "summary_only"
        remaining_budget = _recompute()

    return {
        "trimmed_input_payload": trimmed_payload,
        "budget_report": {
            "model_context_limit": policy.model_context_limit,
            "system_prompt_tokens": current_counts["system_prompt_tokens"],
            "task_input_tokens": current_counts["task_input_tokens"],
            "dependency_summary_tokens": current_counts["dependency_summary_tokens"],
            "global_background_tokens": current_counts["global_background_tokens"],
            "result_summary_tokens": current_counts["result_summary_tokens"],
            "validation_rule_tokens": current_counts["validation_rule_tokens"],
            "history_background_tokens": current_counts["history_background_tokens"],
            "estimated_input_tokens": current_counts["estimated_input_tokens"],
            "initial_estimated_input_tokens": initial_counts["estimated_input_tokens"],
            "reserved_output_tokens": policy.reserved_output_tokens,
            "safe_budget": max(0, remaining_budget),
            "overflow_risk": remaining_budget < 0,
            "initial_overflow_risk": initial_remaining_budget < 0,
            "trim_applied": bool(trim_steps),
            "trim_steps": trim_steps,
            "degradation_mode": degradation_mode,
            "budget_policy": policy.model_dump(),
        },
    }


def build_budget_report(db: Session, task: TaskORM, agent_role: AgentRoleORM) -> dict[str, Any]:
    return build_execution_budget(db, task, agent_role)["budget_report"]
