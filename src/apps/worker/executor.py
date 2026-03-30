from __future__ import annotations

from datetime import datetime, timezone
from traceback import format_exception

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, joinedload

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


def claim_next_task(db: Session) -> tuple[TaskORM, ExecutionRunORM, AgentRoleORM] | None:
    with db.begin():
        task = db.scalars(
            select(TaskORM)
            .where(TaskORM.status == "queued")
            .order_by(_priority_ordering(), TaskORM.created_at.asc())
            .with_for_update(skip_locked=True)
        ).first()

        while task is not None:
            if task.assigned_agent_role and _dependencies_satisfied(task, db):
                break

            task = db.scalars(
                select(TaskORM)
                .where(TaskORM.status == "queued", TaskORM.id != task.id)
                .order_by(_priority_ordering(), TaskORM.created_at.asc())
                .with_for_update(skip_locked=True)
            ).first()

        if task is None:
            return None

        assignment = db.scalars(
            select(AssignmentORM)
            .options(joinedload(AssignmentORM.agent_role))
            .where(
                AssignmentORM.task_id == task.id,
                AssignmentORM.assignment_status == "active",
            )
            .order_by(AssignmentORM.assigned_at.desc())
            .with_for_update(skip_locked=True)
        ).first()
        if assignment is None:
            return None

        agent_role = assignment.agent_role
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
