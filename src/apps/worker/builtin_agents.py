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
