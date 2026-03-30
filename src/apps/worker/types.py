from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from src.packages.core.db.models import TaskORM


@dataclass(slots=True)
class WorkerContext:
    run_id: str
    task_id: str
    agent_role_name: str
    started_at: datetime


class AgentRunner(Protocol):
    def run(self, task: TaskORM, context: WorkerContext) -> dict:
        ...
