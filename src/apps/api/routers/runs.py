from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.apps.api.deps import get_db
from src.packages.core.db.models import ExecutionRunORM
from src.packages.core.schemas import ExecutionRunRead

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("/{run_id}", response_model=ExecutionRunRead)
def get_run(run_id: str, db: Session = Depends(get_db)) -> ExecutionRunRead:
    run = db.get(ExecutionRunORM, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution run not found")
    return ExecutionRunRead.model_validate(run)
