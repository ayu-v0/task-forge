from __future__ import annotations

from dataclasses import is_dataclass
from typing import Any, Protocol

from src.packages.core.db.models import TaskORM


class WorkerAgent(Protocol):
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        ...


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
        return {
            "status": "ok",
            "task_id": task.id,
            "task_type": task.task_type,
            "echo": task.input_payload,
            "context": _serialize_context(context),
        }


class FailingWorkerAgent:
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(f"Agent execution failed for task {task.id}")


class PlannerWorkerAgent:
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        text = str(task.input_payload.get("text", "")).strip()
        tags = [task.task_type, "demo", "planned"]
        steps = [step for step in text.split(" ") if step]
        return {
            "status": "ok",
            "stage": "planner",
            "task_id": task.id,
            "normalized_text": text,
            "tags": tags,
            "steps": steps,
            "context": _serialize_context(context),
        }


class SearchWorkerAgent:
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        query = str(task.input_payload.get("query", task.input_payload.get("text", ""))).strip()
        keywords = [part for part in query.split(" ") if part]
        return {
            "status": "ok",
            "stage": "search",
            "task_id": task.id,
            "query": query,
            "search_plan": {
                "keywords": keywords,
                "sources": ["web_search", "docs_index"],
                "intent": "research",
            },
            "context": _serialize_context(context),
        }


class CodeWorkerAgent:
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        prompt = str(task.input_payload.get("prompt", task.input_payload.get("text", ""))).strip()
        language = str(task.input_payload.get("language", "python")).strip() or "python"
        summary = prompt if prompt else f"implement task_type={task.task_type}"
        return {
            "status": "ok",
            "stage": "code",
            "task_id": task.id,
            "code_plan": {
                "language": language,
                "summary": summary,
                "steps": [
                    "inspect existing code",
                    "apply minimal change",
                    "run targeted tests",
                ],
            },
            "context": _serialize_context(context),
        }


class DefaultWorkerAgent:
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "ok",
            "stage": "worker",
            "task_id": task.id,
            "result": {
                "summary": f"processed task_type={task.task_type}",
                "input": task.input_payload,
            },
            "context": _serialize_context(context),
        }


class ReviewerWorkerAgent:
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        raw = task.input_payload.get("raw_output")
        validation_passed = bool(raw) or bool(task.input_payload.get("allow_empty"))
        needs_manual_review = bool(task.input_payload.get("force_manual_review")) or not validation_passed
        return {
            "status": "ok",
            "stage": "reviewer",
            "task_id": task.id,
            "validation_passed": validation_passed,
            "needs_manual_review": needs_manual_review,
            "notes": "manual review required due to failed validation"
            if needs_manual_review
            else "auto review passed",
            "context": _serialize_context(context),
        }
