from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.apps.api.deps import get_db
from src.packages.core.db.models import AgentRoleORM
from src.packages.core.schemas import (
    AgentCapabilityDeclaration,
    AgentRoleDetailRead,
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
