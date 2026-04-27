from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.apps.api.deps import get_db
from src.packages.core.costs import estimate_cost
from src.packages.core.db.models import AgentRoleORM, AssignmentORM, EventLogORM, ExecutionRunORM, TaskORM
from src.packages.core.error_classification import classify_run_error
from src.packages.core.schemas import (
    BatchReplayItemRead,
    BatchReplayRead,
    ExecutionRunRead,
    RunDetailRead,
    RunDetailTaskRead,
    RunReplayRead,
    RunRoutingSnapshotRead,
    RunRetryHistoryItemRead,
    RunRoutingRead,
    TaskBatchRead,
    TaskEventRead,
    TaskStatusHistoryItemRead,
)
from src.packages.core.timeline import load_task_timeline
from src.apps.api.routers.task_batches import _derive_batch_status
from src.packages.core.db.models import TaskBatchORM
from src.packages.core.token_budget import build_result_summary

router = APIRouter(tags=["runs"])


def _routing_snapshot_from_events(run: ExecutionRunORM, events: list[EventLogORM]) -> RunRoutingSnapshotRead | None:
    for event in events:
        if event.run_id != run.id or event.event_type != "execution_run_replay_snapshot":
            continue
        payload = event.payload or {}
        return RunRoutingSnapshotRead(
            task_id=payload.get("task_id", run.task_id),
            run_id=payload.get("run_id", run.id),
            assignment_id=payload.get("assignment_id"),
            agent_role_id=payload.get("agent_role_id"),
            agent_role_name=payload.get("agent_role_name"),
            routing_reason=payload.get("routing_reason"),
            task_type=payload.get("task_type"),
            input_snapshot=payload.get("input_snapshot") or {},
            expected_output_schema=payload.get("expected_output_schema") or {},
            dependency_ids=payload.get("dependency_ids") or [],
            task_summary=payload.get("task_summary") or {},
            dependency_summaries=payload.get("dependency_summaries") or [],
        )
    return None


def _execution_run_read(run: ExecutionRunORM) -> ExecutionRunRead:
    payload = ExecutionRunRead.model_validate(run)
    return payload.model_copy(update={"result_summary": build_result_summary(run.output_snapshot, run.error_message)})


def _status_history_from_events(task_id: str, events: list[EventLogORM]) -> list[TaskStatusHistoryItemRead]:
    return [
        TaskStatusHistoryItemRead(
            task_id=event.task_id or task_id,
            old_status=event.payload.get("from_status"),
            new_status=event.payload.get("to_status") or event.event_status or "unknown",
            timestamp=event.created_at,
            reason=event.message,
            actor=event.payload.get("source"),
        )
        for event in events
        if event.event_type == "task_status_changed"
    ]


@router.get("/runs/{run_id}", response_model=ExecutionRunRead)
def get_run(run_id: str, db: Session = Depends(get_db)) -> ExecutionRunRead:
    run = db.get(ExecutionRunORM, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution run not found")
    return _execution_run_read(run)


@router.get("/runs/{run_id}/detail", response_model=RunDetailRead)
def get_run_detail(run_id: str, db: Session = Depends(get_db)) -> RunDetailRead:
    run = db.get(ExecutionRunORM, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution run not found")

    task = db.get(TaskORM, run.task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    assignment = db.scalars(
        select(AssignmentORM)
        .where(AssignmentORM.task_id == task.id)
        .order_by(AssignmentORM.assigned_at.desc(), AssignmentORM.id.desc())
    ).first()

    agent_role_name: str | None = task.assigned_agent_role
    agent_role_id: str | None = assignment.agent_role_id if assignment is not None else None
    if agent_role_id:
        agent_role = db.get(AgentRoleORM, agent_role_id)
        if agent_role is not None:
            agent_role_name = agent_role.role_name

    runs = db.scalars(
        select(ExecutionRunORM)
        .where(ExecutionRunORM.task_id == task.id)
        .order_by(ExecutionRunORM.started_at.desc(), ExecutionRunORM.id.desc())
    ).all()
    events = db.scalars(
        select(EventLogORM)
        .where(EventLogORM.task_id == task.id)
        .order_by(EventLogORM.created_at.asc(), EventLogORM.id.asc())
    ).all()

    return RunDetailRead(
        run=_execution_run_read(run),
        task=RunDetailTaskRead(
            task_id=task.id,
            title=task.title,
            task_type=task.task_type,
            status=task.status,
            assigned_agent_role=task.assigned_agent_role,
            retry_count=task.retry_count,
            batch_id=task.batch_id,
        ),
        routing=RunRoutingRead(
            routing_reason=assignment.routing_reason if assignment is not None else None,
            agent_role_id=agent_role_id,
            agent_role_name=agent_role_name,
        ),
        retry_history=[
            RunRetryHistoryItemRead(
                run_id=item.id,
                run_status=item.run_status,
                started_at=item.started_at,
                finished_at=item.finished_at,
                latency_ms=item.latency_ms,
                error_message=item.error_message,
                is_current=item.id == run.id,
            )
            for item in runs
        ],
        events=[TaskEventRead.model_validate(event) for event in events],
        cost_estimate=estimate_cost(run.token_usage),
        error_category=classify_run_error(
            run_status=run.run_status,
            error_message=run.error_message,
            logs=run.logs,
            routing_reason=assignment.routing_reason if assignment is not None else None,
        ),
        result_summary=build_result_summary(run.output_snapshot, run.error_message),
    )


@router.get("/tasks/{task_id}/runs", response_model=list[ExecutionRunRead])
def list_task_runs(task_id: str, db: Session = Depends(get_db)) -> list[ExecutionRunRead]:
    task = db.get(TaskORM, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    runs = db.scalars(
        select(ExecutionRunORM)
        .where(ExecutionRunORM.task_id == task_id)
        .order_by(ExecutionRunORM.started_at.asc(), ExecutionRunORM.id.asc())
    ).all()
    return [_execution_run_read(run) for run in runs]


@router.get("/runs/{run_id}/replay", response_model=RunReplayRead)
def get_run_replay(run_id: str, db: Session = Depends(get_db)) -> RunReplayRead:
    run = db.get(ExecutionRunORM, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution run not found")

    task = db.get(TaskORM, run.task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    events = db.scalars(
        select(EventLogORM)
        .where(EventLogORM.task_id == task.id)
        .order_by(EventLogORM.created_at.asc(), EventLogORM.id.asc())
    ).all()
    timeline = load_task_timeline(db, task.id)
    if timeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task timeline not found")

    routing_snapshot = _routing_snapshot_from_events(run, events)
    return RunReplayRead(
        run=_execution_run_read(run),
        task=RunDetailTaskRead(
            task_id=task.id,
            title=task.title,
            task_type=task.task_type,
            status=task.status,
            assigned_agent_role=task.assigned_agent_role,
            retry_count=task.retry_count,
            batch_id=task.batch_id,
        ),
        routing_snapshot=routing_snapshot,
        status_history=_status_history_from_events(task.id, events),
        timeline=timeline,
        events=[TaskEventRead.model_validate(event) for event in events],
        replay_ready=routing_snapshot is not None,
    )


@router.get("/task-batches/{batch_id}/replay", response_model=BatchReplayRead)
def get_batch_replay(batch_id: str, db: Session = Depends(get_db)) -> BatchReplayRead:
    batch = db.get(TaskBatchORM, batch_id)
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task batch not found")

    tasks = db.scalars(
        select(TaskORM)
        .where(TaskORM.batch_id == batch_id)
        .order_by(TaskORM.created_at.asc(), TaskORM.id.asc())
    ).all()
    items: list[BatchReplayItemRead] = []
    for task in tasks:
        latest_run = db.scalars(
            select(ExecutionRunORM)
            .where(ExecutionRunORM.task_id == task.id)
            .order_by(ExecutionRunORM.started_at.desc(), ExecutionRunORM.id.desc())
        ).first()
        events = db.scalars(
            select(EventLogORM)
            .where(EventLogORM.task_id == task.id)
            .order_by(EventLogORM.created_at.asc(), EventLogORM.id.asc())
        ).all()
        timeline = load_task_timeline(db, task.id)
        if timeline is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task timeline not found")
        routing_snapshot = _routing_snapshot_from_events(latest_run, events) if latest_run is not None else None
        items.append(
            BatchReplayItemRead(
                task_id=task.id,
                title=task.title,
                task_type=task.task_type,
                status=task.status,
                routing_snapshot=routing_snapshot,
                latest_run=_execution_run_read(latest_run) if latest_run is not None else None,
                timeline=timeline,
            )
        )

    return BatchReplayRead(
        batch=TaskBatchRead.model_validate(batch),
        derived_status=_derive_batch_status(tasks),
        items=items,
    )
