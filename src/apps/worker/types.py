from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Protocol

from src.packages.core.db.models import TaskORM


@dataclass(slots=True)
class WorkerContext:
    run_id: str
    task_id: str
    agent_role_name: str
    started_at: datetime
    cancellation_check: Callable[[], bool] = field(repr=False)

    def is_cancellation_requested(self) -> bool:
        return self.cancellation_check()


class AgentRunner(Protocol):
    def run(self, task: TaskORM, context: WorkerContext) -> dict:
        ...
