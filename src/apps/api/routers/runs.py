from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.apps.api.deps import get_db
from src.packages.core.costs import estimate_cost
from src.packages.core.db.models import AgentRoleORM, AssignmentORM, EventLogORM, ExecutionRunORM, TaskORM
from src.packages.core.schemas import (
    ExecutionRunRead,
    RunDetailRead,
    RunDetailTaskRead,
    RunRetryHistoryItemRead,
    RunRoutingRead,
    TaskEventRead,
)

router = APIRouter(tags=["runs"])


@router.get("/runs/{run_id}", response_model=ExecutionRunRead)
def get_run(run_id: str, db: Session = Depends(get_db)) -> ExecutionRunRead:
    run = db.get(ExecutionRunORM, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution run not found")
    return ExecutionRunRead.model_validate(run)


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
        run=ExecutionRunRead.model_validate(run),
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
    return [ExecutionRunRead.model_validate(run) for run in runs]
