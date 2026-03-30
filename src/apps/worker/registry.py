from __future__ import annotations

from src.apps.worker.types import AgentRunner, WorkerContext
from src.packages.core.db.models import TaskORM


class DefaultWorkerAgent:
    def run(self, task: TaskORM, context: WorkerContext) -> dict:
        return {
            "status": "ok",
            "task_id": task.id,
            "run_id": context.run_id,
            "agent_role": context.agent_role_name,
            "echo": task.input_payload,
        }


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, AgentRunner] = {}

    def register(self, role_name: str, agent: AgentRunner) -> None:
        self._agents[role_name] = agent

    def get(self, role_name: str) -> AgentRunner | None:
        return self._agents.get(role_name)


def build_default_registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry.register("default_worker", DefaultWorkerAgent())
    return registry
