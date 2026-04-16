from __future__ import annotations

import json
from math import ceil
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.packages.core.db.models import AgentRoleORM, ExecutionRunORM, TaskORM


DEFAULT_MODEL_CONTEXT_LIMIT = 128_000
MIN_RESERVED_OUTPUT_TOKENS = 256
JSON_SEPARATORS = (",", ":")


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=JSON_SEPARATORS)


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, ceil(len(text) / 4))


def _system_prompt_source(task: TaskORM, agent_role: AgentRoleORM) -> str:
    return _stable_json(
        {
            "role_name": agent_role.role_name,
            "description": agent_role.description or "",
            "capabilities": agent_role.capabilities or [],
            "input_schema": agent_role.input_schema or {},
            "output_schema": agent_role.output_schema or {},
            "task_type": task.task_type,
        }
    )


def _dependency_summary_source(db: Session, task: TaskORM) -> str:
    if not task.dependency_ids:
        return ""

    dependency_tasks = db.scalars(
        select(TaskORM)
        .where(TaskORM.id.in_(task.dependency_ids))
        .order_by(TaskORM.created_at.asc(), TaskORM.id.asc())
    ).all()

    items: list[dict[str, Any]] = []
    for dependency in dependency_tasks:
        latest_run = db.scalars(
            select(ExecutionRunORM)
            .where(ExecutionRunORM.task_id == dependency.id)
            .order_by(ExecutionRunORM.started_at.desc(), ExecutionRunORM.id.desc())
        ).first()
        output_keys = sorted((latest_run.output_snapshot or {}).keys()) if latest_run is not None else []
        items.append(
            {
                "task_id": dependency.id,
                "title": dependency.title,
                "status": dependency.status,
                "assigned_agent_role": dependency.assigned_agent_role,
                "latest_output_keys": output_keys,
            }
        )
    return _stable_json(items)


def _reserved_output_tokens(task: TaskORM) -> int:
    schema_text = _stable_json(task.expected_output_schema or {})
    return max(MIN_RESERVED_OUTPUT_TOKENS, estimate_text_tokens(schema_text) * 2)


def build_budget_report(db: Session, task: TaskORM, agent_role: AgentRoleORM) -> dict[str, Any]:
    system_prompt_tokens = estimate_text_tokens(_system_prompt_source(task, agent_role))
    task_input_tokens = estimate_text_tokens(_stable_json(task.input_payload or {}))
    dependency_summary_tokens = estimate_text_tokens(_dependency_summary_source(db, task))
    estimated_input_tokens = system_prompt_tokens + task_input_tokens + dependency_summary_tokens
    reserved_output_tokens = _reserved_output_tokens(task)
    remaining_budget = DEFAULT_MODEL_CONTEXT_LIMIT - estimated_input_tokens - reserved_output_tokens

    return {
        "model_context_limit": DEFAULT_MODEL_CONTEXT_LIMIT,
        "system_prompt_tokens": system_prompt_tokens,
        "task_input_tokens": task_input_tokens,
        "dependency_summary_tokens": dependency_summary_tokens,
        "estimated_input_tokens": estimated_input_tokens,
        "reserved_output_tokens": reserved_output_tokens,
        "safe_budget": max(0, remaining_budget),
        "overflow_risk": remaining_budget < 0,
    }
