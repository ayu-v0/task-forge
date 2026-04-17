from __future__ import annotations

from dataclasses import is_dataclass
from typing import Any, Protocol

from src.packages.core.db.models import TaskORM


class WorkerAgent(Protocol):
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        ...


def _build_output(
    *,
    status: str,
    summary: str,
    result: dict[str, Any],
    warnings: list[str] | None = None,
    next_action_hint: str | None = None,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "status": status,
        "summary": summary,
        "result": result,
        "warnings": warnings or [],
        "next_action_hint": next_action_hint,
    }
    if extras:
        payload.update(extras)
    return payload


def _serialize_context(context: Any) -> dict[str, Any]:
    if isinstance(context, dict):
        return context

    if is_dataclass(context):
        return {
            "run_id": getattr(context, "run_id", None),
            "task_id": getattr(context, "task_id", None),
            "agent_role_name": getattr(context, "agent_role_name", None),
            "started_at": getattr(context, "started_at", None).isoformat()
            if getattr(context, "started_at", None) is not None
            else None,
        }

    return {"value": str(context)}


class EchoWorkerAgent:
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        result = {
            "task_id": task.id,
            "task_type": task.task_type,
            "echo": task.input_payload,
            "context": _serialize_context(context),
        }
        return _build_output(
            status="ok",
            summary=f"echoed task_type={task.task_type}",
            result=result,
            next_action_hint="inspect echoed payload",
            extras=result,
        )


class FailingWorkerAgent:
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(f"Agent execution failed for task {task.id}")


class PlannerWorkerAgent:
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        text = str(task.input_payload.get("text", "")).strip()
        tags = [task.task_type, "demo", "planned"]
        steps = [step for step in text.split(" ") if step]
        result = {
            "normalized_text": text,
            "tags": tags,
            "steps": steps,
            "context": _serialize_context(context),
        }
        return _build_output(
            status="ok",
            summary=f"planned {len(steps)} step(s) for task_type={task.task_type}",
            result=result,
            next_action_hint="execute planned steps",
            extras={"stage": "planner", "task_id": task.id, **result},
        )


class SearchWorkerAgent:
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        query = str(task.input_payload.get("query", task.input_payload.get("text", ""))).strip()
        keywords = [part for part in query.split(" ") if part]
        search_plan = {
            "keywords": keywords,
            "sources": ["web_search", "docs_index"],
            "intent": "research",
        }
        result = {
            "query": query,
            "search_plan": search_plan,
            "context": _serialize_context(context),
        }
        return _build_output(
            status="ok",
            summary=f"prepared research plan for query={query or task.task_type}",
            result=result,
            next_action_hint="run search against listed sources",
            extras={"stage": "search", "task_id": task.id, **result},
        )


class CodeWorkerAgent:
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        prompt = str(task.input_payload.get("prompt", task.input_payload.get("text", ""))).strip()
        language = str(task.input_payload.get("language", "python")).strip() or "python"
        summary = prompt if prompt else f"implement task_type={task.task_type}"
        code_plan = {
            "language": language,
            "summary": summary,
            "steps": [
                "inspect existing code",
                "apply minimal change",
                "run targeted tests",
            ],
        }
        result = {
            "code_plan": code_plan,
            "context": _serialize_context(context),
        }
        return _build_output(
            status="ok",
            summary=summary,
            result=result,
            next_action_hint="implement the code plan",
            extras={"stage": "code", "task_id": task.id, **result},
        )


class DefaultWorkerAgent:
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        worker_result = {
            "task_type": task.task_type,
            "input": task.input_payload,
            "context": _serialize_context(context),
        }
        return _build_output(
            status="ok",
            summary=f"processed task_type={task.task_type}",
            result=worker_result,
            next_action_hint="review processed output",
            extras={"stage": "worker", "task_id": task.id, "context": _serialize_context(context)},
        )


class ReviewerWorkerAgent:
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        raw = task.input_payload.get("raw_output")
        structured_output = task.input_payload.get("structured_output")
        if not structured_output:
            downstream = task.input_payload.get("downstream_summary") or []
            if isinstance(downstream, list):
                for item in downstream:
                    if isinstance(item, dict) and item.get("structured_output"):
                        structured_output = item["structured_output"]
                        break
        candidate_output = raw if raw is not None else structured_output
        validation_passed = bool(candidate_output) or bool(task.input_payload.get("allow_empty"))
        needs_manual_review = bool(task.input_payload.get("force_manual_review")) or not validation_passed
        notes = "manual review required due to failed validation" if needs_manual_review else "auto review passed"
        result = {
            "validation_passed": validation_passed,
            "needs_manual_review": needs_manual_review,
            "notes": notes,
            "reviewed_output": candidate_output,
            "context": _serialize_context(context),
        }
        return _build_output(
            status="ok",
            summary=notes,
            result=result,
            warnings=[] if validation_passed else ["validation failed or empty output"],
            next_action_hint="manual review required" if needs_manual_review else "proceed to next task",
            extras={
                "stage": "reviewer",
                "task_id": task.id,
                "validation_passed": validation_passed,
                "needs_manual_review": needs_manual_review,
                "notes": notes,
                "context": _serialize_context(context),
            },
        )
