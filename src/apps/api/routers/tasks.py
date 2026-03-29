from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.apps.api.deps import get_db
from src.packages.core.db.models import EventLogORM, TaskORM
from src.packages.core.schemas import TaskEventRead, TaskRead

router = APIRouter(prefix="/tasks", tags=["tasks"])


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
