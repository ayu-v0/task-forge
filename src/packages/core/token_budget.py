from __future__ import annotations

import json
from math import ceil
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.packages.core.db.models import AgentRoleORM, ExecutionRunORM, TaskORM
from src.packages.core.schemas import PromptBudgetPolicyRead


JSON_SEPARATORS = (",", ":")


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


def _task_input_source(task: TaskORM) -> str:
    return _stable_json(task.input_payload or {})


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


def build_budget_report(db: Session, task: TaskORM, agent_role: AgentRoleORM) -> dict[str, Any]:
    policy = _load_policy(agent_role)

    global_background_tokens = _capped_tokens(
        _global_background_source(task, agent_role),
        policy.max_global_background_tokens,
    )
    task_input_tokens = _capped_tokens(
        _task_input_source(task),
        policy.max_task_input_tokens,
    )
    dependency_summary_tokens = _capped_tokens(
        _dependency_summary_source(db, task),
        policy.max_dependency_summary_tokens,
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
        policy.max_history_background_tokens,
    )

    estimated_input_tokens = (
        global_background_tokens
        + task_input_tokens
        + dependency_summary_tokens
        + result_summary_tokens
        + validation_rule_tokens
        + history_background_tokens
    )
    system_prompt_tokens = global_background_tokens + validation_rule_tokens
    remaining_budget = policy.model_context_limit - estimated_input_tokens - policy.reserved_output_tokens

    return {
        "model_context_limit": policy.model_context_limit,
        "system_prompt_tokens": system_prompt_tokens,
        "task_input_tokens": task_input_tokens,
        "dependency_summary_tokens": dependency_summary_tokens,
        "global_background_tokens": global_background_tokens,
        "result_summary_tokens": result_summary_tokens,
        "validation_rule_tokens": validation_rule_tokens,
        "history_background_tokens": history_background_tokens,
        "estimated_input_tokens": estimated_input_tokens,
        "reserved_output_tokens": policy.reserved_output_tokens,
        "safe_budget": max(0, remaining_budget),
        "overflow_risk": remaining_budget < 0,
        "budget_policy": policy.model_dump(),
    }
