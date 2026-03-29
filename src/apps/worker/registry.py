from __future__ import annotations

from src.apps.worker.builtin_agents import EchoWorkerAgent, FailingWorkerAgent, WorkerAgent


def get_worker_agent(role_name: str) -> WorkerAgent:
    agents: dict[str, WorkerAgent] = {
        "default_worker": EchoWorkerAgent(),
        "echo_worker": EchoWorkerAgent(),
        "failing_worker": FailingWorkerAgent(),
    }

    if role_name not in agents:
        raise KeyError(f"No worker agent registered for role {role_name}")

    return agents[role_name]
