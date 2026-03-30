from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.apps.api.deps import get_db
from src.packages.core.db.models import EventLogORM, TaskORM
from src.packages.core.schemas import TaskCancelRequest, TaskEventRead, TaskRead
from src.packages.core.task_state_machine import TaskStatusTransitionError, transition_task_status

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/{task_id}", response_model=TaskRead)
def get_task(task_id: str, db: Session = Depends(get_db)) -> TaskRead:
    task = db.get(TaskORM, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskRead.model_validate(task)


@router.get("/{task_id}/events", response_model=list[TaskEventRead])
def get_task_events(task_id: str, db: Session = Depends(get_db)) -> list[TaskEventRead]:
    task = db.get(TaskORM, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    events = db.scalars(
        select(EventLogORM)
        .where(EventLogORM.task_id == task_id)
        .order_by(EventLogORM.created_at.asc())
    ).all()
    return [TaskEventRead.model_validate(event) for event in events]


@router.post("/{task_id}/cancel", response_model=TaskRead)
def cancel_task(
    task_id: str,
    payload: TaskCancelRequest,
    db: Session = Depends(get_db),
) -> TaskRead:
    task = db.get(TaskORM, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    if task.status in {"success", "failed", "cancelled"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task in status {task.status} cannot be cancelled",
        )

    if task.status == "running" and task.cancellation_requested:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Task cancellation has already been requested",
        )

    task.cancellation_requested = True
    task.cancellation_requested_at = _now()
    task.cancellation_reason = payload.reason

    db.add(
        EventLogORM(
            batch_id=task.batch_id,
            task_id=task.id,
            event_type="task_cancellation_requested",
            event_status=task.status,
            message=payload.reason,
            payload={
                "task_id": task.id,
                "reason": payload.reason,
                "requested_at": task.cancellation_requested_at.isoformat(),
                "source": "api",
            },
        )
    )

    if task.status != "running":
        try:
            transition_task_status(
                db,
                task,
                to_status="cancelled",
                reason=payload.reason,
                source="api",
            )
        except TaskStatusTransitionError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

        db.add(
            EventLogORM(
                batch_id=task.batch_id,
                task_id=task.id,
                event_type="task_cancellation_completed",
                event_status="cancelled",
                message=payload.reason,
                payload={
                    "task_id": task.id,
                    "reason": payload.reason,
                    "completed_at": _now().isoformat(),
                    "source": "api",
                },
            )
        )

    db.commit()
    db.refresh(task)
    return TaskRead.model_validate(task)
