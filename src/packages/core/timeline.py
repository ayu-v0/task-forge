from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.packages.core.db.models import EventLogORM, TaskBatchORM, TaskORM
from src.packages.core.schemas import BatchTimelineRead, TaskTimelineRead, TimelineItemRead


@dataclass(frozen=True)
class _SortableTimelineItem:
    item: TimelineItemRead
    event_id: str
    sequence: int


def _timeline_item(
    *,
    timestamp,
    stage: str,
    title: str,
    detail: str | None,
    task_id: str | None,
    run_id: str | None,
    status: str | None,
    actor: str | None,
) -> TimelineItemRead:
    return TimelineItemRead(
        timestamp=timestamp,
        stage=stage,
        title=title,
        detail=detail,
        task_id=task_id,
        run_id=run_id,
        status=status,
        actor=actor,
    )


def _status_stage(to_status: str) -> tuple[str, str]:
    mapping = {
        "queued": ("queued", "Task queued"),
        "blocked": ("blocked", "Task blocked"),
        "running": ("running", "Task started"),
        "success": ("completed", "Task completed"),
        "failed": ("failed", "Task failed"),
        "cancelled": ("cancelled", "Task cancelled"),
        "needs_review": ("review", "Task sent to review"),
    }
    return mapping.get(to_status, ("status", f"Task moved to {to_status}"))


def build_task_timeline(task: TaskORM, events: list[EventLogORM]) -> TaskTimelineRead:
    sortable_items: list[_SortableTimelineItem] = [
        _SortableTimelineItem(
            item=_timeline_item(
                timestamp=task.created_at,
                stage="created",
                title="Task created",
                detail=task.description,
                task_id=task.id,
                run_id=None,
                status="pending",
                actor="system",
            ),
            event_id=f"created-{task.id}",
            sequence=0,
        )
    ]

    for event in events:
        payload = event.payload or {}
        task_id = event.task_id or task.id
        actor = payload.get("source")
        detail = event.message
        sequence = 0

        if event.event_type == "task_status_changed":
            from_status = payload.get("from_status")
            to_status = payload.get("to_status") or event.event_status or "unknown"

            if actor == "router":
                sortable_items.append(
                    _SortableTimelineItem(
                        item=_timeline_item(
                            timestamp=event.created_at,
                            stage="routed",
                            title="Task routed",
                            detail=detail,
                            task_id=task_id,
                            run_id=event.run_id,
                            status=to_status,
                            actor=actor,
                        ),
                        event_id=event.id,
                        sequence=sequence,
                    )
                )
                sequence += 1

            if from_status == "failed" and to_status == "queued":
                sortable_items.append(
                    _SortableTimelineItem(
                        item=_timeline_item(
                            timestamp=event.created_at,
                            stage="retry",
                            title="Retry requested",
                            detail=detail,
                            task_id=task_id,
                            run_id=event.run_id,
                            status=to_status,
                            actor=actor,
                        ),
                        event_id=event.id,
                        sequence=sequence,
                    )
                )
                sequence += 1

            stage, title = _status_stage(to_status)
            sortable_items.append(
                _SortableTimelineItem(
                    item=_timeline_item(
                        timestamp=event.created_at,
                        stage=stage,
                        title=title,
                        detail=detail,
                        task_id=task_id,
                        run_id=event.run_id,
                        status=to_status,
                        actor=actor,
                    ),
                    event_id=event.id,
                    sequence=sequence,
                )
            )
            continue

        stage_title_mapping = {
            "review_checkpoint_created": ("review", "Approval requested"),
            "review_approved": ("review", "Review approved"),
            "review_rejected": ("review", "Review rejected"),
            "review_reassigned": ("review", "Review reassigned"),
            "task_review_resolved": ("review", "Review resolved"),
            "execution_run_replay_snapshot": ("running", "Replay snapshot saved"),
            "execution_run_started": ("running", "Execution started"),
            "execution_run_finished": (
                "completed" if event.event_status == "success" else "failed",
                "Execution finished" if event.event_status == "success" else "Execution failed",
            ),
            "execution_run_cancelled": ("cancelled", "Execution cancelled"),
            "task_cancellation_requested": ("cancelled", "Cancellation requested"),
            "task_cancellation_completed": ("cancelled", "Cancellation completed"),
            "task_unblocked": ("queued", "Dependencies resolved"),
        }
        if event.event_type not in stage_title_mapping:
            continue
        stage, title = stage_title_mapping[event.event_type]
        sortable_items.append(
            _SortableTimelineItem(
                item=_timeline_item(
                    timestamp=event.created_at,
                    stage=stage,
                    title=title,
                    detail=detail,
                    task_id=task_id,
                    run_id=event.run_id,
                    status=event.event_status,
                    actor=actor,
                ),
                event_id=event.id,
                sequence=0,
            )
        )

    items = [
        sortable.item
        for sortable in sorted(
            sortable_items,
            key=lambda entry: (entry.item.timestamp, entry.event_id, entry.sequence),
        )
    ]
    return TaskTimelineRead(task_id=task.id, batch_id=task.batch_id, items=items)


def load_task_timeline(db: Session, task_id: str) -> TaskTimelineRead | None:
    task = db.get(TaskORM, task_id)
    if task is None:
        return None

    events = db.scalars(
        select(EventLogORM)
        .where(EventLogORM.task_id == task.id)
        .order_by(EventLogORM.created_at.asc(), EventLogORM.id.asc())
    ).all()
    return build_task_timeline(task, events)


def load_batch_timeline(db: Session, batch_id: str) -> BatchTimelineRead | None:
    batch = db.get(TaskBatchORM, batch_id)
    if batch is None:
        return None

    tasks = db.scalars(
        select(TaskORM)
        .where(TaskORM.batch_id == batch.id)
        .order_by(TaskORM.created_at.asc(), TaskORM.id.asc())
    ).all()

    sortable_items: list[_SortableTimelineItem] = [
        _SortableTimelineItem(
            item=_timeline_item(
                timestamp=batch.created_at,
                stage="created",
                title="Batch created",
                detail=batch.description,
                task_id=None,
                run_id=None,
                status=batch.status,
                actor=batch.created_by,
            ),
            event_id=f"created-{batch.id}",
            sequence=0,
        )
    ]

    for task in tasks:
        task_timeline = load_task_timeline(db, task.id)
        if task_timeline is None:
            continue
        for index, item in enumerate(task_timeline.items):
            sortable_items.append(
                _SortableTimelineItem(
                    item=item,
                    event_id=f"{task.id}-{index}",
                    sequence=index,
                )
            )

    items = [
        sortable.item
        for sortable in sorted(
            sortable_items,
            key=lambda entry: (entry.item.timestamp, entry.event_id, entry.sequence),
        )
    ]
    return BatchTimelineRead(batch_id=batch.id, title=batch.title, items=items)
