from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.apps.api.deps import get_db
from src.packages.core.db.models import AgentRoleORM, AssignmentORM, EventLogORM, ReviewCheckpointORM, TaskORM
from src.packages.core.schemas import (
    ReviewCheckpointRead,
    ReviewDecisionApproveRequest,
    ReviewDecisionRejectRequest,
    TaskRead,
)
from src.packages.core.task_state_machine import TaskStatusTransitionError, transition_task_status

router = APIRouter(tags=["reviews"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dependencies_satisfied(task: TaskORM, db: Session) -> bool:
    if not task.dependency_ids:
        return True

    satisfied_count = db.scalar(
        select(func.count())
        .select_from(TaskORM)
        .where(TaskORM.id.in_(task.dependency_ids), TaskORM.status == "success")
    )
    return satisfied_count == len(task.dependency_ids)


def _get_review_or_404(db: Session, review_id: str) -> ReviewCheckpointORM:
    review = db.get(ReviewCheckpointORM, review_id)
    if review is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review checkpoint not found")
    return review


def _validate_review_pending(review: ReviewCheckpointORM, task: TaskORM) -> None:
    if review.review_status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Review checkpoint in status {review.review_status} cannot be decided",
        )
    if task.status != "needs_review":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task in status {task.status} cannot be reviewed",
        )


@router.get("/tasks/{task_id}/reviews", response_model=list[ReviewCheckpointRead])
def list_task_reviews(task_id: str, db: Session = Depends(get_db)) -> list[ReviewCheckpointRead]:
    task = db.get(TaskORM, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    reviews = db.scalars(
        select(ReviewCheckpointORM)
        .where(ReviewCheckpointORM.task_id == task_id)
        .order_by(ReviewCheckpointORM.created_at.asc(), ReviewCheckpointORM.id.asc())
    ).all()
    return [ReviewCheckpointRead.model_validate(review) for review in reviews]


@router.get("/reviews/{review_id}", response_model=ReviewCheckpointRead)
def get_review(review_id: str, db: Session = Depends(get_db)) -> ReviewCheckpointRead:
    review = _get_review_or_404(db, review_id)
    return ReviewCheckpointRead.model_validate(review)


@router.post("/reviews/{review_id}/approve", response_model=TaskRead)
def approve_review(
    review_id: str,
    payload: ReviewDecisionApproveRequest,
    db: Session = Depends(get_db),
) -> TaskRead:
    review = _get_review_or_404(db, review_id)
    task = db.get(TaskORM, review.task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    _validate_review_pending(review, task)

    agent_role = db.get(AgentRoleORM, payload.agent_role_id)
    if agent_role is None or not agent_role.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Approved review requires an enabled agent role",
        )

    review.review_status = "approved"
    review.reviewer = payload.reviewer
    review.review_comment = payload.review_comment
    review.resolved_at = _now()

    task.assigned_agent_role = agent_role.role_name
    assignment = AssignmentORM(
        task_id=task.id,
        agent_role_id=agent_role.id,
        routing_reason=f"manually approved by {payload.reviewer}",
        assignment_status="active",
    )
    db.add(assignment)

    next_status = "queued" if _dependencies_satisfied(task, db) else "blocked"
    next_reason = (
        f"review approved by {payload.reviewer}; task queued for execution"
        if next_status == "queued"
        else f"review approved by {payload.reviewer}; waiting for dependencies to complete"
    )

    try:
        transition_task_status(
            db,
            task,
            to_status=next_status,
            reason=next_reason,
            source="review",
        )
    except TaskStatusTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    db.add(
        EventLogORM(
            batch_id=task.batch_id,
            task_id=task.id,
            event_type="review_approved",
            event_status=task.status,
            message=payload.review_comment or review.reason,
            payload={
                "task_id": task.id,
                "review_id": review.id,
                "reviewer": payload.reviewer,
                "review_comment": payload.review_comment,
                "agent_role_id": agent_role.id,
                "agent_role_name": agent_role.role_name,
                "resolved_at": review.resolved_at.isoformat(),
                "next_status": next_status,
                "source": "review",
            },
        )
    )
    db.add(
        EventLogORM(
            batch_id=task.batch_id,
            task_id=task.id,
            event_type="task_review_resolved",
            event_status=task.status,
            message="review approved",
            payload={
                "task_id": task.id,
                "review_id": review.id,
                "decision": "approved",
                "reviewer": payload.reviewer,
                "resolved_at": review.resolved_at.isoformat(),
                "source": "review",
            },
        )
    )

    db.commit()
    db.refresh(task)
    return TaskRead.model_validate(task)


@router.post("/reviews/{review_id}/reject", response_model=TaskRead)
def reject_review(
    review_id: str,
    payload: ReviewDecisionRejectRequest,
    db: Session = Depends(get_db),
) -> TaskRead:
    review = _get_review_or_404(db, review_id)
    task = db.get(TaskORM, review.task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    _validate_review_pending(review, task)

    review.review_status = "rejected"
    review.reviewer = payload.reviewer
    review.review_comment = payload.review_comment
    review.resolved_at = _now()

    try:
        transition_task_status(
            db,
            task,
            to_status="failed",
            reason=f"review rejected by {payload.reviewer}",
            source="review",
        )
    except TaskStatusTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    db.add(
        EventLogORM(
            batch_id=task.batch_id,
            task_id=task.id,
            event_type="review_rejected",
            event_status=task.status,
            message=payload.review_comment,
            payload={
                "task_id": task.id,
                "review_id": review.id,
                "reviewer": payload.reviewer,
                "review_comment": payload.review_comment,
                "resolved_at": review.resolved_at.isoformat(),
                "source": "review",
            },
        )
    )
    db.add(
        EventLogORM(
            batch_id=task.batch_id,
            task_id=task.id,
            event_type="task_review_resolved",
            event_status=task.status,
            message="review rejected",
            payload={
                "task_id": task.id,
                "review_id": review.id,
                "decision": "rejected",
                "reviewer": payload.reviewer,
                "resolved_at": review.resolved_at.isoformat(),
                "source": "review",
            },
        )
    )

    db.commit()
    db.refresh(task)
    return TaskRead.model_validate(task)
