from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.apps.api.deps import get_db
from src.packages.core.db.models import TaskBatchORM
from src.packages.core.schemas import TaskBatchCreate, TaskBatchRead

router = APIRouter(prefix="/task-batches", tags=["task-batches"])


@router.post("", response_model=TaskBatchRead, status_code=status.HTTP_201_CREATED)
def create_task_batch(payload: TaskBatchCreate, db: Session = Depends(get_db)) -> TaskBatchRead:
    task_batch = TaskBatchORM(
        title=payload.title,
        description=payload.description,
        created_by=payload.created_by,
        status="draft",
        total_tasks=0,
        metadata_json=payload.metadata,
    )
    db.add(task_batch)
    db.commit()
    db.refresh(task_batch)
    return TaskBatchRead.model_validate(task_batch)


@router.get("/{batch_id}", response_model=TaskBatchRead)
def get_task_batch(batch_id: str, db: Session = Depends(get_db)) -> TaskBatchRead:
    task_batch = db.get(TaskBatchORM, batch_id)
    if task_batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task batch not found")
    return TaskBatchRead.model_validate(task_batch)
