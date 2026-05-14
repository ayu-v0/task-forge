from __future__ import annotations

import json
from pathlib import PurePosixPath
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from src.apps.api.deps import get_db
from src.packages.core.db.models import ArtifactORM
from src.packages.core.schemas import ArtifactRead

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


def _artifact_read(artifact: ArtifactORM) -> ArtifactRead:
    return ArtifactRead(
        id=artifact.id,
        task_id=artifact.task_id,
        run_id=artifact.run_id,
        artifact_type=artifact.artifact_type,
        deliverable_type=(artifact.metadata_json or {}).get("deliverable_type"),
        uri=artifact.uri,
        content_type=artifact.content_type,
        raw_content=artifact.raw_content,
        summary=artifact.summary,
        structured_output=artifact.structured_output,
        metadata=artifact.metadata_json,
        schema_version=artifact.schema_version,
        created_at=artifact.created_at,
    )


def _download_text(artifact: ArtifactORM) -> str:
    raw_content = artifact.raw_content or {}
    if artifact.artifact_type == "code_file":
        return str(raw_content.get("content", ""))
    if artifact.artifact_type == "code_patch":
        return str(raw_content.get("diff", ""))
    if artifact.artifact_type == "test_report":
        return str(raw_content.get("output", ""))
    if artifact.artifact_type in {"document", "analysis_report", "data_file"}:
        return str(raw_content.get("content", raw_content.get("body", "")))
    return json.dumps(raw_content, ensure_ascii=False, indent=2)


def _download_filename(artifact: ArtifactORM) -> str:
    raw_content = artifact.raw_content or {}
    path = str(raw_content.get("path") or "").strip().replace("\\", "/")
    name = PurePosixPath(path).name if path else ""
    if name:
        return name
    metadata = artifact.metadata_json or {}
    deliverable_type = str(metadata.get("deliverable_type") or "").strip().lower()
    extensions = {
        "markdown": ".md",
        "txt": ".txt",
        "code": ".txt",
        "json": ".json",
    }
    if artifact.artifact_type == "code_patch":
        extension = ".diff"
    else:
        extension = extensions.get(deliverable_type) or ".json"
    return f"{artifact.task_id or artifact.id}-{artifact.artifact_type}{extension}"


@router.get("/{artifact_id}", response_model=ArtifactRead)
def get_artifact(artifact_id: str, db: Session = Depends(get_db)) -> ArtifactRead:
    artifact = db.get(ArtifactORM, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    return _artifact_read(artifact)


@router.get("/{artifact_id}/download")
def download_artifact(artifact_id: str, db: Session = Depends(get_db)) -> Response:
    artifact = db.get(ArtifactORM, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    filename = _download_filename(artifact)
    content_type = artifact.content_type or "application/octet-stream"
    return Response(
        content=_download_text(artifact),
        media_type=content_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
