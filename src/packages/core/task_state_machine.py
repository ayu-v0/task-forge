from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.packages.core.db.models import EventLogORM, TaskORM


TERMINAL_TASK_STATUSES = {"success", "cancelled"}
TASK_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"queued", "blocked", "needs_review", "cancelled"},
    "queued": {"running", "cancelled", "blocked", "needs_review"},
    "running": {"success", "failed", "needs_review", "cancelled"},
    "blocked": {"queued", "cancelled"},
    "needs_review": {"queued", "blocked", "failed", "cancelled"},
    "failed": {"queued"},
    "success": set(),
    "cancelled": set(),
}


class TaskStatusTransitionError(ValueError):
    pass


def is_valid_task_transition(from_status: str, to_status: str) -> bool:
    return to_status in TASK_STATUS_TRANSITIONS.get(from_status, set())


def transition_task_status(
    db: Session,
    task: TaskORM,
    to_status: str,
    reason: str,
    source: str,
    run_id: str | None = None,
) -> TaskORM:
    # Every valid task status transition emits a canonical task_status_changed event.
    # Domain-specific events (for example review checkpoint lifecycle events) are additive.
    from_status = task.status
    if not is_valid_task_transition(from_status, to_status):
        raise TaskStatusTransitionError(
            f"Illegal task status transition: {from_status} -> {to_status}"
        )

    task.status = to_status
    task.updated_at = datetime.now(timezone.utc)

    db.add(
        EventLogORM(
            batch_id=task.batch_id,
            task_id=task.id,
            run_id=run_id,
            event_type="task_status_changed",
            event_status=to_status,
            message=reason,
            payload={
                "from_status": from_status,
                "to_status": to_status,
                "source": source,
                "task_id": task.id,
                "run_id": run_id,
            },
        )
    )
    return task
