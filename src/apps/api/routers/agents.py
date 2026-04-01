from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.apps.api.deps import get_db
from src.packages.core.db.models import AgentRoleORM, ExecutionRunORM
from src.packages.core.costs import estimate_cost
from src.packages.core.schemas import (
    AgentCapabilityDeclaration,
    AgentRoleDetailRead,
    AgentRegistryDiagnosisRead,
    AgentRegistryListItemRead,
    AgentRegistryResponse,
    AgentRoleRegisterRequest,
    AgentRoleUpdateRequest,
)

router = APIRouter(prefix="/agents", tags=["agents"])


def _build_capability_declaration(agent_role: AgentRoleORM) -> AgentCapabilityDeclaration:
    return AgentCapabilityDeclaration(
        supported_task_types=agent_role.input_schema.get("supported_task_types", []),
        input_requirements=agent_role.input_schema.get("input_requirements", {}),
        output_contract=agent_role.output_schema.get("output_contract", {}),
        supports_concurrency=agent_role.input_schema.get("supports_concurrency", False),
        allows_auto_retry=agent_role.input_schema.get("allows_auto_retry", False),
    )


def _to_agent_detail(agent_role: AgentRoleORM) -> AgentRoleDetailRead:
    return AgentRoleDetailRead(
        id=agent_role.id,
        role_name=agent_role.role_name,
        description=agent_role.description,
        capabilities=agent_role.capabilities,
        capability_declaration=_build_capability_declaration(agent_role),
        input_schema=agent_role.input_schema,
        output_schema=agent_role.output_schema,
        timeout_seconds=agent_role.timeout_seconds,
        max_retries=agent_role.max_retries,
        enabled=agent_role.enabled,
        version=agent_role.version,
    )


def _to_agent_registry_item(
    agent_role: AgentRoleORM,
    *,
    stats: dict[str, Any],
) -> AgentRegistryListItemRead:
    total_runs = int(stats.get("total_runs", 0))
    success_runs = int(stats.get("success_runs", 0))
    success_rate = None
    if total_runs > 0:
        success_rate = round((success_runs / total_runs) * 100, 2)

    return AgentRegistryListItemRead(
        id=agent_role.id,
        role_name=agent_role.role_name,
        description=agent_role.description,
        capabilities=agent_role.capabilities,
        capability_declaration=_build_capability_declaration(agent_role),
        input_schema=agent_role.input_schema,
        output_schema=agent_role.output_schema,
        enabled=agent_role.enabled,
        version=agent_role.version,
        total_runs=total_runs,
        success_runs=success_runs,
        success_rate=success_rate,
        average_latency_ms=stats.get("average_latency_ms"),
        retry_rate=stats.get("retry_rate"),
        average_prompt_tokens=stats.get("average_prompt_tokens", 0),
        average_completion_tokens=stats.get("average_completion_tokens", 0),
        average_total_tokens=stats.get("average_total_tokens", 0),
        total_prompt_tokens=stats.get("total_prompt_tokens", 0),
        total_completion_tokens=stats.get("total_completion_tokens", 0),
        total_tokens=stats.get("total_tokens", 0),
        average_cost_estimate=stats.get("average_cost_estimate", 0),
        total_cost_estimate=stats.get("total_cost_estimate", 0),
    )


def _merge_input_schema(
    base_schema: dict,
    capability_declaration: AgentCapabilityDeclaration,
) -> dict:
    merged = dict(base_schema)
    merged["supported_task_types"] = capability_declaration.supported_task_types
    merged["input_requirements"] = capability_declaration.input_requirements
    merged["supports_concurrency"] = capability_declaration.supports_concurrency
    merged["allows_auto_retry"] = capability_declaration.allows_auto_retry
    return merged


def _merge_output_schema(
    base_schema: dict,
    capability_declaration: AgentCapabilityDeclaration,
) -> dict:
    merged = dict(base_schema)
    merged["output_contract"] = capability_declaration.output_contract
    return merged


def _supported_task_types(agent_role: AgentRoleORM) -> list[str]:
    supported = agent_role.input_schema.get("supported_task_types", [])
    if isinstance(supported, list):
        return [str(item) for item in supported]
    return []


def _build_registry_diagnosis(
    agent_roles: list[AgentRoleORM],
    task_type: str,
) -> AgentRegistryDiagnosisRead:
    matching_enabled_roles: list[str] = []
    matching_disabled_roles: list[str] = []

    for agent_role in agent_roles:
        if task_type not in _supported_task_types(agent_role):
            continue
        if agent_role.enabled:
            matching_enabled_roles.append(agent_role.role_name)
        else:
            matching_disabled_roles.append(agent_role.role_name)

    if matching_enabled_roles:
        status_name = "matched_enabled"
        message = f"Found {len(matching_enabled_roles)} enabled role(s) for task_type={task_type}."
    elif matching_disabled_roles:
        status_name = "matched_disabled_only"
        message = (
            f"No enabled role can execute task_type={task_type}; "
            "matching roles exist but are disabled."
        )
    else:
        status_name = "no_match"
        message = f"No agent role declares support for task_type={task_type}."

    return AgentRegistryDiagnosisRead(
        task_type=task_type,
        status=status_name,
        message=message,
        matching_enabled_roles=matching_enabled_roles,
        matching_disabled_roles=matching_disabled_roles,
    )


@router.post("/register", response_model=AgentRoleDetailRead, status_code=status.HTTP_201_CREATED)
def register_agent(
    payload: AgentRoleRegisterRequest,
    db: Session = Depends(get_db),
) -> AgentRoleDetailRead:
    existing_agent = db.scalar(
        select(AgentRoleORM).where(AgentRoleORM.role_name == payload.role_name)
    )
    if existing_agent is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Agent role {payload.role_name} already exists",
        )

    agent_role = AgentRoleORM(
        role_name=payload.role_name,
        description=payload.description,
        capabilities=payload.capabilities,
        input_schema=_merge_input_schema(payload.input_schema, payload.capability_declaration),
        output_schema=_merge_output_schema(payload.output_schema, payload.capability_declaration),
        timeout_seconds=payload.timeout_seconds,
        max_retries=payload.max_retries,
        enabled=payload.enabled,
        version=payload.version,
    )
    db.add(agent_role)
    db.commit()
    db.refresh(agent_role)
    return _to_agent_detail(agent_role)


@router.get("", response_model=list[AgentRoleDetailRead])
def list_agents(db: Session = Depends(get_db)) -> list[AgentRoleDetailRead]:
    agent_roles = db.scalars(select(AgentRoleORM).order_by(AgentRoleORM.role_name)).all()
    return [_to_agent_detail(agent_role) for agent_role in agent_roles]


@router.get("/registry", response_model=AgentRegistryResponse)
def get_agent_registry(
    task_type: str | None = None,
    db: Session = Depends(get_db),
) -> AgentRegistryResponse:
    agent_roles = db.scalars(select(AgentRoleORM).order_by(AgentRoleORM.role_name)).all()

    runs = db.scalars(select(ExecutionRunORM).order_by(ExecutionRunORM.started_at.asc(), ExecutionRunORM.id.asc())).all()
    run_stats_by_role_id: dict[str, dict[str, Any]] = {}
    task_run_counts: dict[str, int] = {}
    for run in runs:
        task_run_counts[run.task_id] = task_run_counts.get(run.task_id, 0) + 1

    for run in runs:
        stats = run_stats_by_role_id.setdefault(
            run.agent_role_id,
            {
                "total_runs": 0,
                "success_runs": 0,
                "latency_sum": 0,
                "latency_count": 0,
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_tokens": 0,
                "total_cost_estimate": 0.0,
                "retry_runs": 0,
            },
        )
        stats["total_runs"] += 1
        if run.run_status == "success":
            stats["success_runs"] += 1
        if run.latency_ms is not None:
            stats["latency_sum"] += int(run.latency_ms)
            stats["latency_count"] += 1
        prompt_tokens = int(run.token_usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(run.token_usage.get("completion_tokens", 0) or 0)
        total_tokens = int(run.token_usage.get("total_tokens", 0) or 0)
        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens
        stats["total_prompt_tokens"] += prompt_tokens
        stats["total_completion_tokens"] += completion_tokens
        stats["total_tokens"] += total_tokens
        stats["total_cost_estimate"] += estimate_cost(run.token_usage)
        if task_run_counts.get(run.task_id, 0) > 1:
            stats["retry_runs"] += 1

    for stats in run_stats_by_role_id.values():
        total_runs = stats["total_runs"]
        latency_count = stats["latency_count"]
        stats["average_latency_ms"] = (
            round(stats["latency_sum"] / latency_count, 2) if latency_count else None
        )
        stats["retry_rate"] = round((stats["retry_runs"] / total_runs) * 100, 2) if total_runs else None
        stats["average_prompt_tokens"] = round(stats["total_prompt_tokens"] / total_runs, 2) if total_runs else 0
        stats["average_completion_tokens"] = (
            round(stats["total_completion_tokens"] / total_runs, 2) if total_runs else 0
        )
        stats["average_total_tokens"] = round(stats["total_tokens"] / total_runs, 2) if total_runs else 0
        stats["total_cost_estimate"] = round(stats["total_cost_estimate"], 6)
        stats["average_cost_estimate"] = round(stats["total_cost_estimate"] / total_runs, 6) if total_runs else 0

    items = [
        _to_agent_registry_item(
            agent_role,
            stats=run_stats_by_role_id.get(agent_role.id, {}),
        )
        for agent_role in agent_roles
    ]

    diagnosis = None
    normalized_task_type = task_type.strip() if task_type else ""
    if normalized_task_type:
        diagnosis = _build_registry_diagnosis(agent_roles, normalized_task_type)

    return AgentRegistryResponse(items=items, diagnosis=diagnosis)


@router.get("/{agent_id}", response_model=AgentRoleDetailRead)
def get_agent(agent_id: str, db: Session = Depends(get_db)) -> AgentRoleDetailRead:
    agent_role = db.get(AgentRoleORM, agent_id)
    if agent_role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent role not found")
    return _to_agent_detail(agent_role)


@router.patch("/{agent_id}", response_model=AgentRoleDetailRead)
def update_agent(
    agent_id: str,
    payload: AgentRoleUpdateRequest,
    db: Session = Depends(get_db),
) -> AgentRoleDetailRead:
    agent_role = db.get(AgentRoleORM, agent_id)
    if agent_role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent role not found")

    update_data = payload.model_dump(exclude_unset=True)

    if "description" in update_data:
        agent_role.description = update_data["description"]
    if "capabilities" in update_data:
        agent_role.capabilities = update_data["capabilities"]
    if "timeout_seconds" in update_data:
        agent_role.timeout_seconds = update_data["timeout_seconds"]
    if "max_retries" in update_data:
        agent_role.max_retries = update_data["max_retries"]
    if "enabled" in update_data:
        agent_role.enabled = update_data["enabled"]
    if "version" in update_data:
        agent_role.version = update_data["version"]

    capability_declaration = payload.capability_declaration or _build_capability_declaration(agent_role)

    if "input_schema" in update_data or "capability_declaration" in update_data:
        base_input_schema = update_data.get("input_schema", agent_role.input_schema)
        agent_role.input_schema = _merge_input_schema(base_input_schema, capability_declaration)

    if "output_schema" in update_data or "capability_declaration" in update_data:
        base_output_schema = update_data.get("output_schema", agent_role.output_schema)
        agent_role.output_schema = _merge_output_schema(base_output_schema, capability_declaration)

    db.commit()
    db.refresh(agent_role)
    return _to_agent_detail(agent_role)
