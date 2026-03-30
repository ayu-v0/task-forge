from __future__ import annotations

from datetime import datetime, timezone
from traceback import format_exception

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from src.apps.worker.registry import AgentRegistry
from src.apps.worker.types import WorkerContext
from src.packages.core.db.models import AgentRoleORM, AssignmentORM, EventLogORM, ExecutionRunORM, TaskORM
from src.packages.core.task_state_machine import transition_task_status


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


def claim_next_task(db: Session) -> tuple[TaskORM, ExecutionRunORM, AgentRoleORM] | None:
    with db.begin():
        task = None
        queued_tasks = db.scalars(
            select(TaskORM)
            .where(
                TaskORM.status == "queued",
                TaskORM.assigned_agent_role.is_not(None),
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
        run = ExecutionRunORM(
            task_id=task.id,
            agent_role_id=assignment.agent_role_id,
            run_status="running",
            started_at=_now(),
            logs=["claimed by worker"],
            input_snapshot=task.input_payload,
            output_snapshot={},
            token_usage={},
        )
        db.add(run)
        db.flush()

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
    with db.begin():
        task = db.get(TaskORM, task_id)
        run = db.get(ExecutionRunORM, run_id)
        if task is None or run is None:
            raise ValueError("Task or run not found")

        run.run_status = "success"
        run.finished_at = _now()
        run.output_snapshot = result
        run.latency_ms = latency_ms
        run.logs = [*run.logs, "execution finished"]

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


def mark_run_failed(
    db: Session,
    task_id: str,
    run_id: str,
    exc: Exception,
    latency_ms: int,
) -> ExecutionRunORM:
    with db.begin():
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

        context = WorkerContext(
            run_id=run.id,
            task_id=task.id,
            agent_role_name=agent_role.role_name,
            started_at=started_at,
        )
        result = agent.run(task, context)
        latency_ms = max(0, int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000))
        return mark_run_success(db, task.id, run.id, result, latency_ms)
    except Exception as exc:
        latency_ms = max(0, int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000))
        return mark_run_failed(db, task.id, run.id, exc, latency_ms)


def run_next_task(db: Session, registry: AgentRegistry) -> ExecutionRunORM | None:
    claimed = claim_next_task(db)
    if claimed is None:
        return None
    task, run, agent_role = claimed
    return execute_task(db, registry, task, run, agent_role)
