from __future__ import annotations

from typing import Any, Protocol

from src.packages.core.db.models import TaskORM


class WorkerAgent(Protocol):
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        ...


class EchoWorkerAgent:
    def run(self, task: TaskORM, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "ok",
            "task_id": task.id,
            "task_type": task.task_type,
            "echo": task.input_payload,
            "context": context,
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
            "context": context,
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
            "context": context,
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
            "context": context,
        }
