from __future__ import annotations

from dataclasses import is_dataclass
from typing import Any, Protocol

from src.apps.worker.http_model_agent import run_model_agent_if_enabled
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


def _structured_output(
    *,
    status: str,
    summary: str,
    result: dict[str, Any],
    warnings: list[str] | None = None,
    next_action_hint: str | None = None,
    **legacy_fields: Any,
) -> dict[str, Any]:
    payload = {
        "status": status,
        "summary": summary,
        "result": result,
        "warnings": warnings or [],
        "next_action_hint": next_action_hint,
    }
    payload.update(legacy_fields)
    return payload


def _review_target(raw_output: Any) -> Any:
    if isinstance(raw_output, dict) and {
        "status",
        "summary",
        "result",
        "warnings",
        "next_action_hint",
    }.issubset(raw_output):
        return raw_output.get("result")
    return raw_output


class EchoWorkerAgent:
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        http_result = run_model_agent_if_enabled("echo_worker", task, context)
        if http_result is not None:
            return http_result
        result = {
            "stage": "echo",
            "task_id": task.id,
            "task_type": task.task_type,
            "echo": task.input_payload,
            "context": _serialize_context(context),
        }
        summary = f"echoed task_type={task.task_type}"
        return {
            **_structured_output(
                status="ok",
                summary=summary,
                result=result,
                warnings=[],
                next_action_hint="Inspect echoed payload for downstream debugging or smoke tests.",
            ),
            "task_id": task.id,
            "task_type": task.task_type,
            "echo": task.input_payload,
            "context": result["context"],
        }


class FailingWorkerAgent:
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(f"Agent execution failed for task {task.id}")


class PlannerWorkerAgent:
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        http_result = run_model_agent_if_enabled("planner_agent", task, context)
        if http_result is not None:
            return http_result
        text = str(task.input_payload.get("text", "")).strip()
        tags = [task.task_type, "demo", "planned"]
        steps = [step for step in text.split(" ") if step]
        result = {
            "stage": "planner",
            "task_id": task.id,
            "normalized_text": text,
            "tags": tags,
            "steps": steps,
            "context": _serialize_context(context),
        }
        return _structured_output(
            status="ok",
            summary=f"planned {len(steps)} step(s) for task_type={task.task_type}",
            result=result,
            warnings=[],
            next_action_hint="Use the normalized text and steps as the worker input baseline.",
            stage="planner",
            task_id=task.id,
            normalized_text=text,
            tags=tags,
            steps=steps,
            context=result["context"],
        )


class SearchWorkerAgent:
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        http_result = run_model_agent_if_enabled("search_agent", task, context)
        if http_result is not None:
            return http_result
        query = str(task.input_payload.get("query", task.input_payload.get("text", ""))).strip()
        keywords = [part for part in query.split(" ") if part]
        search_plan = {
            "keywords": keywords,
            "sources": ["web_search", "docs_index"],
            "intent": "research",
        }
        result = {
            "stage": "search",
            "task_id": task.id,
            "query": query,
            "search_plan": search_plan,
            "context": _serialize_context(context),
        }
        return _structured_output(
            status="ok",
            summary=f"prepared research plan with {len(keywords)} keyword(s)",
            result=result,
            warnings=[],
            next_action_hint="Execute the listed sources before drafting the final response.",
            stage="search",
            task_id=task.id,
            query=query,
            search_plan=search_plan,
            context=result["context"],
        )


class CodeWorkerAgent:
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        http_result = run_model_agent_if_enabled("code_agent", task, context)
        if http_result is not None:
            return http_result
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
            "stage": "code",
            "task_id": task.id,
            "code_plan": code_plan,
            "context": _serialize_context(context),
        }
        return _structured_output(
            status="ok",
            summary=f"prepared {language} implementation plan",
            result=result,
            warnings=[],
            next_action_hint="Implement the change and verify with targeted tests.",
            stage="code",
            task_id=task.id,
            code_plan=code_plan,
            context=result["context"],
        )


class DefaultWorkerAgent:
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        http_result = run_model_agent_if_enabled("worker_agent", task, context)
        if http_result is not None:
            return http_result
        worker_result = {
            "stage": "worker",
            "task_id": task.id,
            "payload": {
                "summary": f"processed task_type={task.task_type}",
                "input": task.input_payload,
            },
            "context": _serialize_context(context),
        }
        return _structured_output(
            status="ok",
            summary=f"processed task_type={task.task_type}",
            result=worker_result,
            warnings=[],
            next_action_hint="Pass the worker payload to reviewer or downstream aggregation.",
            stage="worker",
            task_id=task.id,
            context=worker_result["context"],
        )


class ReviewerWorkerAgent:
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        http_result = run_model_agent_if_enabled("reviewer_agent", task, context)
        if http_result is not None:
            return http_result
        raw = task.input_payload.get("raw_output")
        review_target = _review_target(raw)
        validation_passed = bool(review_target) or bool(task.input_payload.get("allow_empty"))
        needs_manual_review = bool(task.input_payload.get("force_manual_review")) or not validation_passed
        notes = (
            "manual review required due to failed validation"
            if needs_manual_review
            else "auto review passed"
        )
        result = {
            "stage": "reviewer",
            "task_id": task.id,
            "validation_passed": validation_passed,
            "needs_manual_review": needs_manual_review,
            "notes": notes,
            "review_target": review_target,
            "context": _serialize_context(context),
        }
        return _structured_output(
            status="needs_review" if needs_manual_review else "ok",
            summary=notes,
            result=result,
            warnings=[notes] if needs_manual_review else [],
            next_action_hint="Manual review is required before accepting this result."
            if needs_manual_review
            else "Promote the reviewed result to downstream consumers.",
            stage="reviewer",
            task_id=task.id,
            validation_passed=validation_passed,
            needs_manual_review=needs_manual_review,
            notes=notes,
            context=result["context"],
        )
