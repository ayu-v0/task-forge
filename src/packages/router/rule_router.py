from __future__ import annotations

from dataclasses import dataclass

from src.packages.core.db.models import AgentRoleORM, TaskORM


@dataclass(frozen=True)
class RouteResult:
    agent_role_id: str | None
    agent_role_name: str | None
    routing_reason: str
    auto_execute: bool
    needs_review: bool


def _sorted_roles(agent_roles: list[AgentRoleORM]) -> list[AgentRoleORM]:
    return sorted(
        [role for role in agent_roles if role.enabled],
        key=lambda role: role.role_name,
    )


def _match_by_task_type(task: TaskORM, agent_roles: list[AgentRoleORM]) -> AgentRoleORM | None:
    for role in _sorted_roles(agent_roles):
        supported_task_types = role.input_schema.get("supported_task_types", [])
        if task.task_type in supported_task_types:
            return role
    return None


def _match_by_capability(task: TaskORM, agent_roles: list[AgentRoleORM]) -> AgentRoleORM | None:
    target_capability = f"task:{task.task_type}"
    for role in _sorted_roles(agent_roles):
        if target_capability in role.capabilities:
            return role
    return None


def _schema_compatible(task: TaskORM, role: AgentRoleORM) -> bool:
    input_requirements = role.input_schema.get("input_requirements", {})
    expected_output = role.output_schema.get("output_contract", {})

    required_properties = input_requirements.get("properties", {})
    expected_output_type = expected_output.get("type")

    # Schema compatibility should only apply when the role actually declares a schema contract.
    if not required_properties and not expected_output_type:
        return False

    if required_properties:
        if not set(task.input_payload.keys()).issubset(set(required_properties.keys())):
            return False

    task_output_type = task.expected_output_schema.get("type")
    if expected_output_type and task_output_type and expected_output_type != task_output_type:
        return False

    return True


def _match_by_schema(task: TaskORM, agent_roles: list[AgentRoleORM]) -> AgentRoleORM | None:
    for role in _sorted_roles(agent_roles):
        if _schema_compatible(task, role):
            return role
    return None


def _match_default_role(agent_roles: list[AgentRoleORM]) -> AgentRoleORM | None:
    for role in _sorted_roles(agent_roles):
        if role.role_name == "default_worker" or "default_worker" in role.capabilities:
            return role
    return None


def route_task(task: TaskORM, agent_roles: list[AgentRoleORM]) -> RouteResult:
    matched_role = _match_by_task_type(task, agent_roles)
    if matched_role is not None:
        return RouteResult(
            agent_role_id=matched_role.id,
            agent_role_name=matched_role.role_name,
            routing_reason=f"matched by task_type={task.task_type}",
            auto_execute=True,
            needs_review=False,
        )

    matched_role = _match_by_capability(task, agent_roles)
    if matched_role is not None:
        return RouteResult(
            agent_role_id=matched_role.id,
            agent_role_name=matched_role.role_name,
            routing_reason=f"matched by capability=task:{task.task_type}",
            auto_execute=True,
            needs_review=False,
        )

    matched_role = _match_by_schema(task, agent_roles)
    if matched_role is not None:
        return RouteResult(
            agent_role_id=matched_role.id,
            agent_role_name=matched_role.role_name,
            routing_reason="matched by schema compatibility",
            auto_execute=True,
            needs_review=False,
        )

    matched_role = _match_default_role(agent_roles)
    if matched_role is not None:
        return RouteResult(
            agent_role_id=matched_role.id,
            agent_role_name=matched_role.role_name,
            routing_reason="fallback to default_worker",
            auto_execute=True,
            needs_review=False,
        )

    return RouteResult(
        agent_role_id=None,
        agent_role_name=None,
        routing_reason=f"No eligible agent role found for task_type={task.task_type}",
        auto_execute=False,
        needs_review=True,
    )
