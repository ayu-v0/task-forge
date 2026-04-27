from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.apps.api.deps import get_db
from src.packages.core.db.models import ArtifactORM
from src.packages.core.schemas import ArtifactRead

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("/{artifact_id}", response_model=ArtifactRead)
def get_artifact(artifact_id: str, db: Session = Depends(get_db)) -> ArtifactRead:
    artifact = db.get(ArtifactORM, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    return ArtifactRead.model_validate(artifact)
