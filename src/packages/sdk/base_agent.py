from __future__ import annotations

from abc import ABC, abstractmethod

from src.apps.worker.types import WorkerContext
from src.packages.core.db.models import TaskORM


class BaseAgent(ABC):
    role_name: str = ""
    capabilities: list[str] = []

    def validate_input(self, task: TaskORM) -> None:
        return None

    @abstractmethod
    def run(self, task: TaskORM, context: WorkerContext) -> dict:
        raise NotImplementedError

    def validate_output(self, result: dict) -> None:
        if not isinstance(result, dict):
            raise ValueError("Agent output must be a dict")

    def on_error(self, task: TaskORM, context: WorkerContext, exc: Exception) -> None:
        return None

    def execute(self, task: TaskORM, context: WorkerContext) -> dict:
        try:
            self.validate_input(task)
            result = self.run(task, context)
            self.validate_output(result)
            return result
        except Exception as exc:
            self.on_error(task, context, exc)
            raise
