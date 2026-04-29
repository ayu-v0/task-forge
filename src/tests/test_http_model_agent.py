from __future__ import annotations

from datetime import datetime, timezone

from src.apps.worker.http_model_agent import _normalize_payload
from src.apps.worker.types import WorkerContext
from src.packages.core.db.models import TaskORM


def test_normalize_payload_defaults_optional_model_fields() -> None:
    task = TaskORM(
        id="task_1",
        batch_id="batch_1",
        title="write hello world",
        task_type="code",
        input_payload={"prompt": "write hello world", "language": "python"},
        expected_output_schema={"type": "object"},
    )
    context = WorkerContext(
        run_id="run_1",
        task_id="task_1",
        agent_role_name="code_agent",
        started_at=datetime.now(timezone.utc),
        cancellation_check=lambda: False,
    )

    payload = _normalize_payload(
        "code_agent",
        task,
        context,
        {
            "status": "success",
            "summary": "generated hello world",
            "result": {"code": "print('Hello, World!')\n", "language": "python"},
        },
    )

    assert payload["warnings"] == []
    assert payload["next_action_hint"] is None
    assert payload["result"]["code"] == "print('Hello, World!')\n"
    assert payload["stage"] == "code"
