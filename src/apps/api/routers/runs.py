from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.apps.api.deps import get_db
from src.packages.core.db.models import ExecutionRunORM, TaskORM
from src.packages.core.schemas import ExecutionRunRead

router = APIRouter(tags=["runs"])


@router.get("/runs/{run_id}", response_model=ExecutionRunRead)
def get_run(run_id: str, db: Session = Depends(get_db)) -> ExecutionRunRead:
    run = db.get(ExecutionRunORM, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution run not found")
    return ExecutionRunRead.model_validate(run)


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
