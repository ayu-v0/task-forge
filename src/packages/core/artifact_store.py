from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.packages.core.db.models import ArtifactORM
from src.packages.core.token_budget import _build_summary, _strip_system_summary_fields, build_result_summary


def _normalize_raw_content(result: dict[str, Any] | None) -> dict[str, Any]:
    return _strip_system_summary_fields(result)


def build_structured_output(result: dict[str, Any] | None) -> dict[str, Any]:
    raw_content = _normalize_raw_content(result)
    summary = _build_summary(raw_content)
    if isinstance(summary, dict):
        return summary
    return {"value": summary}


def create_run_artifact(
    db: Session,
    *,
    task_id: str,
    run_id: str,
    result: dict[str, Any],
) -> ArtifactORM:
    raw_content = _normalize_raw_content(result)
    artifact_type = str(result.get("artifact_type", "json")).strip() or "json"
    content_type = "application/json" if artifact_type in {"json", "report"} else "text/plain"
    artifact = ArtifactORM(
        task_id=task_id,
        run_id=run_id,
        artifact_type=artifact_type,
        uri="memory://artifacts/pending",
        content_type=content_type,
        raw_content=raw_content,
        summary=build_result_summary(raw_content),
        structured_output=build_structured_output(raw_content),
        metadata_json={
            "source": "execution_run",
            "run_status": "success",
            "output_keys": sorted(raw_content.keys()),
        },
        schema_version="artifact.v1",
    )
    db.add(artifact)
    db.flush()
    artifact.uri = f"memory://artifacts/{artifact.id}"
    return artifact


def load_latest_artifact_for_task(db: Session, task_id: str) -> ArtifactORM | None:
    return db.scalars(
        select(ArtifactORM)
        .where(ArtifactORM.task_id == task_id)
        .order_by(ArtifactORM.created_at.desc(), ArtifactORM.id.desc())
    ).first()
