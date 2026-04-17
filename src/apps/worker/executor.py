from __future__ import annotations

from datetime import datetime, timedelta, timezone
from traceback import format_exception

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from src.apps.worker.registry import AgentRegistry
from src.apps.worker.types import WorkerContext
from src.packages.core.artifact_store import create_run_artifact
from src.packages.core.db.models import AgentRoleORM, AssignmentORM, EventLogORM, ExecutionRunORM, ReviewCheckpointORM, TaskORM
from src.packages.core.task_state_machine import transition_task_status
from src.packages.core.token_budget import build_execution_budget, build_result_summary


class TaskCancelledError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _priority_ordering():
    return case(
        (TaskORM.priority == "urgent", 0),
        (TaskORM.priority == "high", 1),
        (TaskORM.priority == "medium", 2),
        (TaskORM.priority == "low", 3),
        else_=4,
    )


def _dependencies_satisfied(task: TaskORM, db: Session) -> bool:
    if not task.dependency_ids:
        return True

    satisfied_count = db.scalar(
        select(func.count())
        .select_from(TaskORM)
        .where(TaskORM.id.in_(task.dependency_ids), TaskORM.status == "success")
    )
    return satisfied_count == len(task.dependency_ids)


def _review_deadline() -> datetime:
    return _now().replace(microsecond=0) + timedelta(minutes=30)


def _move_task_to_budget_review(
    db: Session,
    *,
    task: TaskORM,
    assignment: AssignmentORM,
    agent_role: AgentRoleORM,
    budget_report: dict,
) -> None:
    reason = (
        f"context trimming exhausted for role={agent_role.role_name}; "
        f"estimated_input_tokens={budget_report['estimated_input_tokens']} "
        f"reserved_output_tokens={budget_report['reserved_output_tokens']}"
    )
    transition_task_status(
        db,
        task,
        to_status="needs_review",
        reason=reason,
        source="worker",
    )
    review_checkpoint = ReviewCheckpointORM(
        task_id=task.id,
        reason=reason,
        reason_category="manual_override",
        timeout_policy="fail_closed",
        review_status="pending",
        deadline_at=_review_deadline(),
    )
    db.add(review_checkpoint)
    db.flush()
    db.add(
        EventLogORM(
            batch_id=task.batch_id,
            task_id=task.id,
            event_type="review_checkpoint_created",
            event_status="needs_review",
            message=reason,
            payload={
                "task_id": task.id,
                "review_id": review_checkpoint.id,
                "reason": reason,
                "reason_category": review_checkpoint.reason_category,
                "timeout_policy": review_checkpoint.timeout_policy,
                "deadline_at": review_checkpoint.deadline_at.isoformat() if review_checkpoint.deadline_at else None,
                "source": "worker",
                "budget_report": budget_report,
                "assignment_id": assignment.id,
                "agent_role_id": agent_role.id,
                "agent_role_name": agent_role.role_name,
            },
        )
    )


def unlock_dependent_tasks(db: Session, completed_task_id: str) -> list[str]:
    blocked_tasks = db.scalars(
        select(TaskORM)
        .where(TaskORM.status == "blocked")
        .order_by(TaskORM.created_at.asc())
    ).all()

    unlocked_task_ids: list[str] = []
    for blocked_task in blocked_tasks:
        if completed_task_id not in blocked_task.dependency_ids:
            continue
        if not blocked_task.assigned_agent_role:
            continue
        if not _dependencies_satisfied(blocked_task, db):
            continue

        assignment = db.scalars(
            select(AssignmentORM)
            .where(
                AssignmentORM.task_id == blocked_task.id,
                AssignmentORM.assignment_status == "active",
            )
            .order_by(AssignmentORM.assigned_at.desc())
        ).first()
        if assignment is None:
            continue

        transition_task_status(
            db,
            blocked_task,
            to_status="queued",
            reason=f"dependencies satisfied after task {completed_task_id} succeeded",
            source="worker",
        )
        db.add(
            EventLogORM(
                batch_id=blocked_task.batch_id,
                task_id=blocked_task.id,
                event_type="task_unblocked",
                event_status="queued",
                message="task dependencies satisfied and task entered execution queue",
                payload={
                    "task_id": blocked_task.id,
                    "completed_dependency_id": completed_task_id,
                    "assignment_id": assignment.id,
                },
            )
        )
        unlocked_task_ids.append(blocked_task.id)

    return unlocked_task_ids


def is_task_cancellation_requested(db: Session, task_id: str) -> bool:
    db.expire_all()
    task = db.get(TaskORM, task_id)
    return bool(task and task.cancellation_requested)


def _execute_agent(agent: object, task: TaskORM, context: WorkerContext) -> dict:
    execute = getattr(agent, "execute", None)
    if callable(execute):
        return execute(task, context)

    run = getattr(agent, "run", None)
    if callable(run):
        return run(task, context)

    raise RuntimeError("Agent runner must provide execute(task, context) or run(task, context)")


def claim_next_task(db: Session) -> tuple[TaskORM, ExecutionRunORM, AgentRoleORM] | None:
    with db.begin():
        task = None
        queued_tasks = db.scalars(
            select(TaskORM)
            .where(
                TaskORM.status == "queued",
                TaskORM.assigned_agent_role.is_not(None),
                TaskORM.cancellation_requested.is_(False),
            )
            .order_by(_priority_ordering(), TaskORM.created_at.asc())
            .with_for_update(skip_locked=True)
        ).all()

        for candidate in queued_tasks:
            if _dependencies_satisfied(candidate, db):
                task = candidate
                break

            transition_task_status(
                db,
                candidate,
                to_status="blocked",
                reason="worker re-blocked task because dependencies are not yet satisfied",
                source="worker",
            )

        if task is None:
            return None

        assignment = db.scalars(
            select(AssignmentORM)
            .where(
                AssignmentORM.task_id == task.id,
                AssignmentORM.assignment_status == "active",
            )
            .order_by(AssignmentORM.assigned_at.desc())
            .with_for_update(skip_locked=True)
        ).first()
        if assignment is None:
            return None

        agent_role = db.get(AgentRoleORM, assignment.agent_role_id)
        if agent_role is None:
            return None
        execution_budget = build_execution_budget(db, task, agent_role)
        budget_report = execution_budget["budget_report"]
        trimmed_input_payload = execution_budget["trimmed_input_payload"]
        if budget_report["overflow_risk"]:
            _move_task_to_budget_review(
                db,
                task=task,
                assignment=assignment,
                agent_role=agent_role,
                budget_report=budget_report,
            )
            return None
        task.input_payload = trimmed_input_payload
        run = ExecutionRunORM(
            task_id=task.id,
            agent_role_id=assignment.agent_role_id,
            run_status="running",
            started_at=_now(),
            logs=[
                "claimed by worker",
                (
                    "budget estimated: "
                    f"input={budget_report['estimated_input_tokens']} "
                    f"reserved_output={budget_report['reserved_output_tokens']} "
                    f"overflow_risk={budget_report['overflow_risk']}"
                ),
            ],
            input_snapshot=trimmed_input_payload,
            output_snapshot={},
            token_usage={},
            budget_report=budget_report,
        )
        db.add(run)
        db.flush()
        if budget_report["trim_applied"]:
            db.add(
                EventLogORM(
                    batch_id=task.batch_id,
                    task_id=task.id,
                    run_id=run.id,
                    event_type="context_trimmed",
                    event_status="running",
                    message="worker trimmed execution context to fit prompt budget",
                    payload={
                        "task_id": task.id,
                        "run_id": run.id,
                        "trim_steps": budget_report["trim_steps"],
                        "degradation_mode": budget_report["degradation_mode"],
                        "source": "worker",
                    },
                )
            )

        transition_task_status(
            db,
            task,
            to_status="running",
            reason="worker claimed queued task",
            source="worker",
            run_id=run.id,
        )
        db.add(
            EventLogORM(
                batch_id=task.batch_id,
                task_id=task.id,
                run_id=run.id,
                event_type="execution_run_replay_snapshot",
                event_status="running",
                message="worker captured replay snapshot for execution run",
                payload={
                    "task_id": task.id,
                    "run_id": run.id,
                    "assignment_id": assignment.id,
                    "agent_role_id": assignment.agent_role_id,
                    "agent_role_name": agent_role.role_name,
                    "routing_reason": assignment.routing_reason,
                    "task_type": task.task_type,
                    "input_snapshot": trimmed_input_payload,
                    "expected_output_schema": task.expected_output_schema,
                    "dependency_ids": task.dependency_ids,
                    "budget_report": budget_report,
                    "source": "worker",
                },
            )
        )
        db.add(
            EventLogORM(
                batch_id=task.batch_id,
                task_id=task.id,
                run_id=run.id,
                event_type="execution_run_started",
                event_status="running",
                message="worker started execution run",
                payload={
                    "task_id": task.id,
                    "run_id": run.id,
                    "agent_role_id": assignment.agent_role_id,
                    "agent_role_name": agent_role.role_name,
                },
            )
        )
        db.flush()
        db.refresh(task)
        db.refresh(run)
        db.refresh(agent_role)
        db.expunge(task)
        db.expunge(run)
        db.expunge(agent_role)
        return task, run, agent_role


def mark_run_success(
    db: Session,
    task_id: str,
    run_id: str,
    result: dict,
    latency_ms: int,
) -> ExecutionRunORM:
    if not db.in_transaction():
        with db.begin():
            return mark_run_success(db, task_id, run_id, result, latency_ms)

    task = db.get(TaskORM, task_id)
    run = db.get(ExecutionRunORM, run_id)
    if task is None or run is None:
        raise ValueError("Task or run not found")
    if task.cancellation_requested:
        return mark_run_cancelled(db, task_id, run_id, task.cancellation_reason or "task cancellation requested")

    final_result = dict(result)
    final_result.setdefault("result_summary", build_result_summary(final_result))

    run.run_status = "success"
    run.finished_at = _now()
    run.output_snapshot = final_result
    run.latency_ms = latency_ms
    run.logs = [*run.logs, "execution finished"]
    create_run_artifact(
        db,
        task_id=task.id,
        run_id=run.id,
        result=final_result,
    )

    transition_task_status(
        db,
        task,
        to_status="success",
        reason="worker finished task successfully",
        source="worker",
        run_id=run.id,
    )
    db.add(
        EventLogORM(
            batch_id=task.batch_id,
            task_id=task.id,
            run_id=run.id,
            event_type="execution_run_finished",
            event_status="success",
            message="worker completed execution run",
            payload={"task_id": task.id, "run_id": run.id},
        )
    )
    unlocked_task_ids = unlock_dependent_tasks(db, task.id)
    if unlocked_task_ids:
        run.logs = [
            *run.logs,
            f"unlocked dependent tasks: {', '.join(unlocked_task_ids)}",
        ]
    db.flush()
    db.refresh(run)
    return run


def mark_run_cancelled(
    db: Session,
    task_id: str,
    run_id: str,
    reason: str,
) -> ExecutionRunORM:
    if not db.in_transaction():
        with db.begin():
            return mark_run_cancelled(db, task_id, run_id, reason)

    task = db.get(TaskORM, task_id)
    run = db.get(ExecutionRunORM, run_id)
    if task is None or run is None:
        raise ValueError("Task or run not found")

    now = _now()
    run.run_status = "cancelled"
    run.finished_at = now
    run.cancelled_at = now
    run.cancel_reason = reason
    run.logs = [*run.logs, "cancellation requested", "execution cancelled"]

    transition_task_status(
        db,
        task,
        to_status="cancelled",
        reason=reason,
        source="worker",
        run_id=run.id,
    )
    db.add(
        EventLogORM(
            batch_id=task.batch_id,
            task_id=task.id,
            run_id=run.id,
            event_type="execution_run_cancelled",
            event_status="cancelled",
            message=reason,
            payload={"task_id": task.id, "run_id": run.id, "reason": reason},
        )
    )
    db.add(
        EventLogORM(
            batch_id=task.batch_id,
            task_id=task.id,
            run_id=run.id,
            event_type="task_cancellation_completed",
            event_status="cancelled",
            message=reason,
            payload={
                "task_id": task.id,
                "run_id": run.id,
                "reason": reason,
                "completed_at": _now().isoformat(),
                "source": "worker",
            },
        )
    )
    db.flush()
    db.refresh(run)
    return run


def mark_run_failed(
    db: Session,
    task_id: str,
    run_id: str,
    exc: Exception,
    latency_ms: int,
) -> ExecutionRunORM:
    if not db.in_transaction():
        with db.begin():
            return mark_run_failed(db, task_id, run_id, exc, latency_ms)

    task = db.get(TaskORM, task_id)
    run = db.get(ExecutionRunORM, run_id)
    if task is None or run is None:
        raise ValueError("Task or run not found")

    error_lines = [line.rstrip() for line in format_exception(exc) if line.strip()]
    run.run_status = "failed"
    run.finished_at = _now()
    run.error_message = str(exc)
    run.latency_ms = latency_ms
    run.logs = [*run.logs, f"execution failed: {exc}", *error_lines]

    transition_task_status(
        db,
        task,
        to_status="failed",
        reason="worker execution failed",
        source="worker",
        run_id=run.id,
    )
    db.add(
        EventLogORM(
            batch_id=task.batch_id,
            task_id=task.id,
            run_id=run.id,
            event_type="execution_run_finished",
            event_status="failed",
            message="worker execution failed",
            payload={"task_id": task.id, "run_id": run.id, "error_message": str(exc)},
        )
    )
    db.flush()
    db.refresh(run)
    return run


def execute_task(
    db: Session,
    registry: AgentRegistry,
    task: TaskORM,
    run: ExecutionRunORM,
    agent_role: AgentRoleORM,
) -> ExecutionRunORM:
    started_at = datetime.now(timezone.utc)
    try:
        if not agent_role.enabled:
            raise RuntimeError(f"Agent role {agent_role.role_name} is disabled")

        agent = registry.get(agent_role.role_name)
        if agent is None:
            raise RuntimeError(f"Agent runner not found for role={agent_role.role_name}")

        with db.begin():
            current_run = db.get(ExecutionRunORM, run.id)
            if current_run is None:
                raise ValueError("Execution run not found")
            current_run.logs = [
                *current_run.logs,
                f"agent loaded: {agent_role.role_name}",
                "execution started",
            ]
            if is_task_cancellation_requested(db, task.id):
                raise TaskCancelledError("task cancellation requested before execution started")

        context = WorkerContext(
            run_id=run.id,
            task_id=task.id,
            agent_role_name=agent_role.role_name,
            started_at=started_at,
            cancellation_check=lambda: is_task_cancellation_requested(db, task.id),
        )
        result = _execute_agent(agent, task, context)
        if is_task_cancellation_requested(db, task.id):
            raise TaskCancelledError("task cancellation requested during execution")
        latency_ms = max(0, int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000))
        if db.in_transaction():
            db.rollback()
        return mark_run_success(db, task.id, run.id, result, latency_ms)
    except TaskCancelledError as exc:
        if db.in_transaction():
            db.rollback()
        return mark_run_cancelled(db, task.id, run.id, str(exc))
    except Exception as exc:
        if is_task_cancellation_requested(db, task.id):
            current_task = db.get(TaskORM, task.id)
            reason = (
                current_task.cancellation_reason
                if current_task and current_task.cancellation_reason
                else "task cancellation requested during execution"
            )
            if db.in_transaction():
                db.rollback()
            return mark_run_cancelled(
                db,
                task.id,
                run.id,
                reason,
            )
        latency_ms = max(0, int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000))
        if db.in_transaction():
            db.rollback()
        return mark_run_failed(db, task.id, run.id, exc, latency_ms)


def run_next_task(db: Session, registry: AgentRegistry) -> ExecutionRunORM | None:
    claimed = claim_next_task(db)
    if claimed is None:
        return None
    task, run, agent_role = claimed
    return execute_task(db, registry, task, run, agent_role)
