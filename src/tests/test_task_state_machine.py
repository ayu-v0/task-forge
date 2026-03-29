from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[2]
STATE_MACHINE_PREFIX = "state-machine-test-"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        env_file = ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("DATABASE_URL="):
                    database_url = line.split("=", 1)[1].strip()
                    break
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")
    return database_url


def _cleanup_database() -> None:
    engine = create_engine(_database_url())
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM task_batches WHERE title LIKE :prefix"),
            {"prefix": f"{STATE_MACHINE_PREFIX}%"},
        )


_cleanup_database()

from src.packages.core.db.models import TaskBatchORM, TaskORM  # noqa: E402
from src.packages.core.task_state_machine import (  # noqa: E402
    TaskStatusTransitionError,
    transition_task_status,
)


def setup_function() -> None:
    _cleanup_database()


def teardown_function() -> None:
    _cleanup_database()


def _create_task(initial_status: str = "pending") -> str:
    engine = create_engine(_database_url())
    suffix = uuid.uuid4().hex[:8]
    with Session(engine) as session:
        batch = TaskBatchORM(
            title=f"{STATE_MACHINE_PREFIX}{suffix}",
            description="state machine batch",
            created_by="pytest",
            status="draft",
            total_tasks=1,
            metadata_json={"suite": "state-machine"},
        )
        session.add(batch)
        session.flush()
        task = TaskORM(
            batch_id=batch.id,
            title=f"{STATE_MACHINE_PREFIX}task-{suffix}",
            description="state machine task",
            task_type="generate",
            priority="medium",
            status=initial_status,
            input_payload={},
            expected_output_schema={},
            assigned_agent_role=None,
            dependency_ids=[],
            retry_count=0,
        )
        session.add(task)
        session.commit()
        return task.id


def test_records_event_log_for_successful_transitions() -> None:
    engine = create_engine(_database_url())
    task_id = _create_task("pending")

    with Session(engine) as session:
        task = session.get(TaskORM, task_id)
        assert task is not None

        transition_task_status(session, task, "queued", "routed to worker", "router")
        transition_task_status(session, task, "running", "worker started", "worker")
        transition_task_status(session, task, "success", "worker finished", "worker")
        session.commit()

    with engine.connect() as conn:
        statuses = conn.execute(
            text(
                "SELECT event_status FROM event_logs "
                "WHERE task_id = :task_id AND event_type = 'task_status_changed' "
                "ORDER BY created_at ASC"
            ),
            {"task_id": task_id},
        ).scalars().all()
        payload = conn.execute(
            text(
                "SELECT payload FROM event_logs "
                "WHERE task_id = :task_id AND event_status = 'queued'"
            ),
            {"task_id": task_id},
        ).scalar_one()

    assert statuses == ["queued", "running", "success"]
    assert payload["from_status"] == "pending"
    assert payload["to_status"] == "queued"
    assert payload["source"] == "router"


def test_supports_retry_transition_from_failed_to_queued() -> None:
    engine = create_engine(_database_url())
    task_id = _create_task("failed")

    with Session(engine) as session:
        task = session.get(TaskORM, task_id)
        assert task is not None

        transition_task_status(session, task, "queued", "retry requested", "review")
        session.commit()

    with engine.connect() as conn:
        status_value = conn.execute(
            text("SELECT status FROM tasks WHERE id = :task_id"),
            {"task_id": task_id},
        ).scalar_one()

    assert status_value == "queued"


def test_rejects_illegal_transition_without_writing_event_log() -> None:
    engine = create_engine(_database_url())
    task_id = _create_task("success")

    with Session(engine) as session:
        task = session.get(TaskORM, task_id)
        assert task is not None

        with pytest.raises(TaskStatusTransitionError):
            transition_task_status(session, task, "running", "should fail", "worker")
        session.rollback()

    with engine.connect() as conn:
        status_value = conn.execute(
            text("SELECT status FROM tasks WHERE id = :task_id"),
            {"task_id": task_id},
        ).scalar_one()
        event_count = conn.execute(
            text(
                "SELECT count(*) FROM event_logs "
                "WHERE task_id = :task_id AND event_type = 'task_status_changed'"
            ),
            {"task_id": task_id},
        ).scalar_one()

    assert status_value == "success"
    assert event_count == 0
