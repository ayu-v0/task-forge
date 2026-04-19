from __future__ import annotations

from typing import Any

from src.packages.core.token_budget import build_result_summary, summarize_value


ARTIFACT_SCHEMA_VERSION = "artifact.v1"
ARTIFACT_ROLE_PRIMARY_OUTPUT = "primary_output"
DEFAULT_ARTIFACT_CONTENT_TYPE = "application/json"


def infer_artifact_type(output_snapshot: dict[str, Any]) -> str:
    if output_snapshot.get("stage") == "reviewer":
        return "review_note"
    if "code_plan" in output_snapshot:
        return "code"
    if "search_plan" in output_snapshot or "artifact" in output_snapshot or output_snapshot.get("stage") == "search":
        return "report"
    if isinstance(output_snapshot, dict):
        return "json"
    return "text"


def build_structured_output(output_snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "keys": sorted(output_snapshot.keys()),
        "field_count": len(output_snapshot),
        "preview": summarize_value(output_snapshot),
    }


def build_artifact_payload(
    *,
    task_id: str,
    run_id: str,
    output_snapshot: dict[str, Any],
) -> dict[str, Any]:
    artifact_type = infer_artifact_type(output_snapshot)
    return {
        "task_id": task_id,
        "run_id": run_id,
        "artifact_type": artifact_type,
        "uri": f"memory://runs/{run_id}/artifacts/primary-output",
        "content_type": DEFAULT_ARTIFACT_CONTENT_TYPE,
        "raw_content": output_snapshot,
        "summary": build_result_summary(output_snapshot),
        "structured_output": build_structured_output(output_snapshot),
        "metadata": {
            "source": "worker",
            "artifact_role": ARTIFACT_ROLE_PRIMARY_OUTPUT,
            "artifact_type": artifact_type,
        },
        "schema_version": ARTIFACT_SCHEMA_VERSION,
    }
