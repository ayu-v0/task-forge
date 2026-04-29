from __future__ import annotations

from dataclasses import dataclass

from src.packages.core.db.models import AgentRoleORM, TaskORM

ROUTING_META_INPUT_KEYS = {"cost_hint", "timeout_seconds"}


@dataclass(frozen=True)
class RouteResult:
    agent_role_id: str | None
    agent_role_name: str | None
    routing_reason: str
    auto_execute: bool
    needs_review: bool


@dataclass(frozen=True)
class RoleRoutingStats:
    total_runs: int = 0
    success_runs: int = 0
    average_latency_ms: float | None = None
    average_cost_estimate: float = 0.0

    @property
    def success_rate(self) -> float | None:
        if self.total_runs <= 0:
            return None
        return self.success_runs / self.total_runs


@dataclass(frozen=True)
class RoleCandidate:
    role: AgentRoleORM
    stats: RoleRoutingStats
    matched_task_type: bool
    matched_capability: bool
    schema_compatible: bool
    is_default_worker: bool
    score: tuple[float, ...]


def _sorted_roles(agent_roles: list[AgentRoleORM]) -> list[AgentRoleORM]:
    return sorted(
        [role for role in agent_roles if role.enabled],
        key=lambda role: role.role_name,
    )


def _supported_task_types(role: AgentRoleORM) -> list[str]:
    supported_task_types = role.input_schema.get("supported_task_types", [])
    if isinstance(supported_task_types, list):
        return [str(item) for item in supported_task_types]
    return []


def _matches_task_type(task: TaskORM, role: AgentRoleORM) -> bool:
    return task.task_type in _supported_task_types(role)


def _matches_capability(task: TaskORM, role: AgentRoleORM) -> bool:
    target_capability = f"task:{task.task_type}"
    return target_capability in role.capabilities


def _schema_compatible(task: TaskORM, role: AgentRoleORM) -> bool:
    input_requirements = role.input_schema.get("input_requirements", {})
    expected_output = role.output_schema.get("output_contract", {})

    required_properties = input_requirements.get("properties", {})
    expected_output_type = expected_output.get("type")

    if not required_properties and not expected_output_type:
        return False

    if required_properties:
        comparable_input_keys = {
            key for key in task.input_payload.keys() if key not in ROUTING_META_INPUT_KEYS
        }
        if not comparable_input_keys.issubset(set(required_properties.keys())):
            return False

    task_output_type = task.expected_output_schema.get("type")
    if expected_output_type and task_output_type and expected_output_type != task_output_type:
        return False

    return True


def _has_schema_contract(role: AgentRoleORM) -> bool:
    input_requirements = role.input_schema.get("input_requirements", {})
    output_contract = role.output_schema.get("output_contract", {})
    return bool(input_requirements.get("properties")) or bool(output_contract.get("type"))


def _is_default_worker(role: AgentRoleORM) -> bool:
    return role.role_name == "default_worker" or "default_worker" in role.capabilities


def _meets_timeout_requirement(task: TaskORM, role: AgentRoleORM) -> bool:
    timeout_required = task.input_payload.get("timeout_seconds")
    if timeout_required is None:
        return True
    try:
        timeout_required_int = int(timeout_required)
    except (TypeError, ValueError):
        return True
    return role.timeout_seconds >= timeout_required_int


def _cost_preference_weight(task: TaskORM, stats: RoleRoutingStats) -> float:
    cost_hint = str(task.input_payload.get("cost_hint", "")).strip().lower()
    average_cost = float(stats.average_cost_estimate or 0.0)

    if average_cost <= 0:
        return 0.0
    if cost_hint == "low":
        return -average_cost * 1000
    if cost_hint == "high":
        return 0.0
    return -average_cost * 100


def _latency_weight(stats: RoleRoutingStats) -> float:
    if stats.average_latency_ms is None:
        return 0.0
    return -float(stats.average_latency_ms) / 1000


def _build_routing_reason(candidate: RoleCandidate) -> str:
    reasons: list[str] = []
    if candidate.matched_task_type:
        reasons.append("task_type")
    if candidate.matched_capability:
        reasons.append("capability")
    if candidate.schema_compatible:
        reasons.append("schema")
    if candidate.is_default_worker and not reasons:
        reasons.append("default_worker")

    success_rate = candidate.stats.success_rate
    if success_rate is None:
        performance_note = "no_history"
    else:
        performance_note = f"success_rate={round(success_rate * 100, 2)}%"

    reason_summary = ",".join(reasons) if reasons else "fallback"
    return (
        f"capability-ranked route selected role={candidate.role.role_name} "
        f"via {reason_summary} ({performance_note})"
    )


def _build_candidate(
    task: TaskORM,
    role: AgentRoleORM,
    stats: RoleRoutingStats,
) -> RoleCandidate | None:
    matched_task_type = _matches_task_type(task, role)
    matched_capability = _matches_capability(task, role)
    schema_compatible = _schema_compatible(task, role)
    default_worker = _is_default_worker(role)

    if not any([matched_task_type, matched_capability, default_worker]):
        return None

    if not _meets_timeout_requirement(task, role):
        return None

    if _has_schema_contract(role) and not schema_compatible:
        return None

    success_rate = stats.success_rate
    score = (
        1.0 if matched_task_type else 0.0,
        1.0 if matched_capability else 0.0,
        1.0 if schema_compatible else 0.0,
        0.0 if default_worker else 1.0,
        success_rate if success_rate is not None else 0.5,
        _cost_preference_weight(task, stats),
        _latency_weight(stats),
        float(role.timeout_seconds),
    )

    return RoleCandidate(
        role=role,
        stats=stats,
        matched_task_type=matched_task_type,
        matched_capability=matched_capability,
        schema_compatible=schema_compatible,
        is_default_worker=default_worker,
        score=score,
    )


def route_task(
    task: TaskORM,
    agent_roles: list[AgentRoleORM],
    role_stats: dict[str, RoleRoutingStats] | None = None,
) -> RouteResult:
    role_stats = role_stats or {}
    candidates: list[RoleCandidate] = []

    for role in _sorted_roles(agent_roles):
        candidate = _build_candidate(
            task,
            role,
            role_stats.get(role.id, RoleRoutingStats()),
        )
        if candidate is not None:
            candidates.append(candidate)

    if candidates:
        candidates.sort(key=lambda candidate: candidate.role.role_name)
        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        selected = candidates[0]
        return RouteResult(
            agent_role_id=selected.role.id,
            agent_role_name=selected.role.role_name,
            routing_reason=_build_routing_reason(selected),
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
