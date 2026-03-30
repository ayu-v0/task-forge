from __future__ import annotations

from src.packages.sdk.base_agent import BaseAgent


class SimpleEchoAgent(BaseAgent):
    role_name = "simple_echo"
    capabilities = ["task:echo", "input:text"]

    def validate_input(self, task) -> None:
        payload = task.input_payload or {}
        if not isinstance(payload.get("text"), str) or not payload["text"].strip():
            raise ValueError("input_payload.text must be a non-empty string")

    def run(self, task, context) -> dict:
        if context.is_cancellation_requested():
            raise RuntimeError("task cancellation requested")
        text = task.input_payload["text"].strip()
        return {
            "status": "ok",
            "task_id": task.id,
            "agent_role": self.role_name,
            "summary": text.upper(),
        }

    def validate_output(self, result: dict) -> None:
        super().validate_output(result)
        if "summary" not in result:
            raise ValueError("result.summary is required")
