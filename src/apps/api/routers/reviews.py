from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.apps.api.deps import get_db
from src.packages.core.db.models import AgentRoleORM, AssignmentORM, EventLogORM, ReviewCheckpointORM, TaskORM
from src.packages.core.schemas import (
    BulkReviewApproveRequest,
    BulkReviewDecisionResponse,
    BulkReviewItemResult,
    BulkReviewReassignRequest,
    BulkReviewRejectRequest,
    ReviewCheckpointRead,
    ReviewDecisionApproveRequest,
    ReviewDecisionReassignRequest,
    ReviewDecisionRejectRequest,
    ReviewTimeoutProcessRequest,
    ReviewTimeoutProcessResponse,
    TaskRead,
)
from src.packages.core.task_state_machine import TaskStatusTransitionError, transition_task_status

router = APIRouter(tags=["reviews"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _next_review_deadline() -> datetime:
    return _now().replace(microsecond=0) + timedelta(minutes=30)


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


def _get_enabled_role(db: Session, agent_role_id: str) -> AgentRoleORM:
    agent_role = db.get(AgentRoleORM, agent_role_id)
    if agent_role is None or not agent_role.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Review decision requires an enabled agent role",
        )
    return agent_role


def _supersede_active_assignments(db: Session, task_id: str) -> None:
    active_assignments = db.scalars(
        select(AssignmentORM)
        .where(
            AssignmentORM.task_id == task_id,
            AssignmentORM.assignment_status == "active",
        )
        .order_by(AssignmentORM.assigned_at.desc(), AssignmentORM.id.desc())
    ).all()
    for assignment in active_assignments:
        assignment.assignment_status = "superseded"


def _apply_assignment(
    db: Session,
    task: TaskORM,
    *,
    agent_role: AgentRoleORM,
    routing_reason: str,
) -> str:
    _supersede_active_assignments(db, task.id)
    task.assigned_agent_role = agent_role.role_name
    db.add(
        AssignmentORM(
            task_id=task.id,
            agent_role_id=agent_role.id,
            routing_reason=routing_reason,
            assignment_status="active",
        )
    )

    next_status = "queued" if _dependencies_satisfied(task, db) else "blocked"
    next_reason = (
        routing_reason if next_status == "queued" else f"{routing_reason}; waiting for dependencies to complete"
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
    return next_status


def _log_review_event(
    db: Session,
    *,
    task: TaskORM,
    event_type: str,
    event_status: str,
    message: str | None,
    payload: dict,
) -> None:
    db.add(
        EventLogORM(
            batch_id=task.batch_id,
            task_id=task.id,
            event_type=event_type,
            event_status=event_status,
            message=message,
            payload=payload,
        )
    )


def _resolve_review(
    review: ReviewCheckpointORM,
    *,
    reviewer: str,
    review_comment: str | None,
    review_status: str,
) -> None:
    review.review_status = review_status
    review.reviewer = reviewer
    review.review_comment = review_comment
    review.resolved_at = _now()


def _approve_task(
    db: Session,
    *,
    review: ReviewCheckpointORM,
    task: TaskORM,
    reviewer: str,
    review_comment: str | None,
    agent_role: AgentRoleORM,
    event_type: str,
    resolution_message: str,
) -> TaskORM:
    _validate_review_pending(review, task)
    _resolve_review(
        review,
        reviewer=reviewer,
        review_comment=review_comment,
        review_status="approved",
    )
    next_status = _apply_assignment(
        db,
        task,
        agent_role=agent_role,
        routing_reason=resolution_message,
    )
    _log_review_event(
        db,
        task=task,
        event_type=event_type,
        event_status=task.status,
        message=review_comment or review.reason,
        payload={
            "task_id": task.id,
            "review_id": review.id,
            "reviewer": reviewer,
            "review_comment": review_comment,
            "agent_role_id": agent_role.id,
            "agent_role_name": agent_role.role_name,
            "reason_category": review.reason_category,
            "timeout_policy": review.timeout_policy,
            "resolved_at": review.resolved_at.isoformat(),
            "next_status": next_status,
            "source": "review",
        },
    )
    _log_review_event(
        db,
        task=task,
        event_type="task_review_resolved",
        event_status=task.status,
        message="review approved" if event_type == "review_approved" else "review reassigned",
        payload={
            "task_id": task.id,
            "review_id": review.id,
            "decision": "approved" if event_type == "review_approved" else "reassigned",
            "reviewer": reviewer,
            "resolved_at": review.resolved_at.isoformat(),
            "source": "review",
        },
    )
    return task


def _reject_task(
    db: Session,
    *,
    review: ReviewCheckpointORM,
    task: TaskORM,
    reviewer: str,
    review_comment: str,
    failure_status: str = "failed",
    event_type: str = "review_rejected",
) -> TaskORM:
    _validate_review_pending(review, task)
    _resolve_review(
        review,
        reviewer=reviewer,
        review_comment=review_comment,
        review_status="rejected",
    )
    try:
        transition_task_status(
            db,
            task,
            to_status=failure_status,
            reason=f"review rejected by {reviewer}",
            source="review",
        )
    except TaskStatusTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _log_review_event(
        db,
        task=task,
        event_type=event_type,
        event_status=task.status,
        message=review_comment,
        payload={
            "task_id": task.id,
            "review_id": review.id,
            "reviewer": reviewer,
            "review_comment": review_comment,
            "reason_category": review.reason_category,
            "timeout_policy": review.timeout_policy,
            "resolved_at": review.resolved_at.isoformat(),
            "source": "review",
        },
    )
    _log_review_event(
        db,
        task=task,
        event_type="task_review_resolved",
        event_status=task.status,
        message="review rejected",
        payload={
            "task_id": task.id,
            "review_id": review.id,
            "decision": "rejected",
            "reviewer": reviewer,
            "resolved_at": review.resolved_at.isoformat(),
            "source": "review",
        },
    )
    return task


def _reassign_task(
    db: Session,
    *,
    review: ReviewCheckpointORM,
    task: TaskORM,
    reviewer: str,
    review_comment: str | None,
    agent_role: AgentRoleORM,
) -> TaskORM:
    return _approve_task(
        db,
        review=review,
        task=task,
        reviewer=reviewer,
        review_comment=review_comment,
        agent_role=agent_role,
        event_type="review_reassigned",
        resolution_message=f"review reassigned by {reviewer}",
    )


def _review_result(task: TaskORM, review_id: str, detail: str | None = None) -> BulkReviewItemResult:
    return BulkReviewItemResult(
        review_id=review_id,
        ok=True,
        task_id=task.id,
        status=task.status,
        assigned_agent_role=task.assigned_agent_role,
        detail=detail,
    )


def _error_result(review_id: str, detail: str) -> BulkReviewItemResult:
    return BulkReviewItemResult(review_id=review_id, ok=False, detail=detail)


def _process_bulk(
    db: Session,
    review_ids: list[str],
    handler,
) -> BulkReviewDecisionResponse:
    items: list[BulkReviewItemResult] = []
    for review_id in review_ids:
        try:
            item = handler(review_id)
            db.commit()
            items.append(item)
        except HTTPException as exc:
            db.rollback()
            items.append(_error_result(review_id, str(exc.detail)))
        except Exception as exc:  # pragma: no cover - defensive branch for API resilience
            db.rollback()
            items.append(_error_result(review_id, str(exc)))
    return BulkReviewDecisionResponse(items=items)


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


@router.post("/reviews/bulk/approve", response_model=BulkReviewDecisionResponse)
def bulk_approve_reviews(
    payload: BulkReviewApproveRequest,
    db: Session = Depends(get_db),
) -> BulkReviewDecisionResponse:
    agent_role = _get_enabled_role(db, payload.agent_role_id)

    def handler(review_id: str) -> BulkReviewItemResult:
        review = _get_review_or_404(db, review_id)
        task = db.get(TaskORM, review.task_id)
        if task is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
        updated_task = _approve_task(
            db,
            review=review,
            task=task,
            reviewer=payload.reviewer,
            review_comment=payload.review_comment,
            agent_role=agent_role,
            event_type="review_approved",
            resolution_message=f"review approved by {payload.reviewer}",
        )
        return _review_result(updated_task, review_id, "approved")

    return _process_bulk(db, payload.review_ids, handler)


@router.post("/reviews/bulk/reject", response_model=BulkReviewDecisionResponse)
def bulk_reject_reviews(
    payload: BulkReviewRejectRequest,
    db: Session = Depends(get_db),
) -> BulkReviewDecisionResponse:
    def handler(review_id: str) -> BulkReviewItemResult:
        review = _get_review_or_404(db, review_id)
        task = db.get(TaskORM, review.task_id)
        if task is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
        updated_task = _reject_task(
            db,
            review=review,
            task=task,
            reviewer=payload.reviewer,
            review_comment=payload.review_comment,
        )
        return _review_result(updated_task, review_id, "rejected")

    return _process_bulk(db, payload.review_ids, handler)


@router.post("/reviews/bulk/reassign", response_model=BulkReviewDecisionResponse)
def bulk_reassign_reviews(
    payload: BulkReviewReassignRequest,
    db: Session = Depends(get_db),
) -> BulkReviewDecisionResponse:
    agent_role = _get_enabled_role(db, payload.agent_role_id)

    def handler(review_id: str) -> BulkReviewItemResult:
        review = _get_review_or_404(db, review_id)
        task = db.get(TaskORM, review.task_id)
        if task is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
        updated_task = _reassign_task(
            db,
            review=review,
            task=task,
            reviewer=payload.reviewer,
            review_comment=payload.review_comment,
            agent_role=agent_role,
        )
        return _review_result(updated_task, review_id, "reassigned")

    return _process_bulk(db, payload.review_ids, handler)


@router.post("/reviews/process-timeouts", response_model=ReviewTimeoutProcessResponse)
def process_review_timeouts(
    payload: ReviewTimeoutProcessRequest,
    db: Session = Depends(get_db),
) -> ReviewTimeoutProcessResponse:
    now = _now()
    reviews = db.scalars(
        select(ReviewCheckpointORM)
        .where(
            ReviewCheckpointORM.review_status == "pending",
            ReviewCheckpointORM.deadline_at.is_not(None),
            ReviewCheckpointORM.deadline_at <= now,
        )
        .order_by(ReviewCheckpointORM.deadline_at.asc(), ReviewCheckpointORM.id.asc())
        .limit(payload.limit)
    ).all()

    items: list[BulkReviewItemResult] = []
    for review in reviews:
        try:
            task = db.get(TaskORM, review.task_id)
            if task is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
            _validate_review_pending(review, task)
            if review.timeout_policy == "cancel_task":
                updated_task = _reject_task(
                    db,
                    review=review,
                    task=task,
                    reviewer="system",
                    review_comment="review timeout policy applied: cancel_task",
                    failure_status="cancelled",
                    event_type="review_timeout_processed",
                )
                items.append(_review_result(updated_task, review.id, "timeout_cancelled"))
            elif review.timeout_policy == "escalate":
                _resolve_review(
                    review,
                    reviewer="system",
                    review_comment="review timeout policy applied: escalate",
                    review_status="rejected",
                )
                db.add(
                    ReviewCheckpointORM(
                        task_id=task.id,
                        reason=f"review escalated after timeout: {review.reason}",
                        reason_category="manual_override",
                        timeout_policy="fail_closed",
                        review_status="pending",
                        deadline_at=_next_review_deadline(),
                    )
                )
                _log_review_event(
                    db,
                    task=task,
                    event_type="review_timeout_processed",
                    event_status=task.status,
                    message=review.review_comment,
                    payload={
                        "task_id": task.id,
                        "review_id": review.id,
                        "policy": review.timeout_policy,
                        "reviewer": "system",
                        "resolved_at": review.resolved_at.isoformat(),
                        "source": "review",
                    },
                )
                _log_review_event(
                    db,
                    task=task,
                    event_type="review_timeout_escalated",
                    event_status=task.status,
                    message=review.review_comment,
                    payload={
                        "task_id": task.id,
                        "review_id": review.id,
                        "policy": review.timeout_policy,
                        "reviewer": "system",
                        "resolved_at": review.resolved_at.isoformat(),
                        "source": "review",
                    },
                )
                _log_review_event(
                    db,
                    task=task,
                    event_type="task_review_resolved",
                    event_status=task.status,
                    message="review timed out and escalated",
                    payload={
                        "task_id": task.id,
                        "review_id": review.id,
                        "decision": "timed_out_escalated",
                        "reviewer": "system",
                        "resolved_at": review.resolved_at.isoformat(),
                        "source": "review",
                    },
                )
                items.append(_review_result(task, review.id, "timeout_escalated"))
            else:
                updated_task = _reject_task(
                    db,
                    review=review,
                    task=task,
                    reviewer="system",
                    review_comment="review timeout policy applied: fail_closed",
                    failure_status="failed",
                    event_type="review_timeout_processed",
                )
                items.append(_review_result(updated_task, review.id, "timeout_failed"))
            db.commit()
        except HTTPException as exc:
            db.rollback()
            items.append(_error_result(review.id, str(exc.detail)))
        except Exception as exc:  # pragma: no cover
            db.rollback()
            items.append(_error_result(review.id, str(exc)))

    return ReviewTimeoutProcessResponse(
        processed_count=sum(1 for item in items if item.ok),
        items=items,
    )


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
    agent_role = _get_enabled_role(db, payload.agent_role_id)
    task = _approve_task(
        db,
        review=review,
        task=task,
        reviewer=payload.reviewer,
        review_comment=payload.review_comment,
        agent_role=agent_role,
        event_type="review_approved",
        resolution_message=f"review approved by {payload.reviewer}",
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
    task = _reject_task(
        db,
        review=review,
        task=task,
        reviewer=payload.reviewer,
        review_comment=payload.review_comment,
    )
    db.commit()
    db.refresh(task)
    return TaskRead.model_validate(task)


@router.post("/reviews/{review_id}/reassign", response_model=TaskRead)
def reassign_review(
    review_id: str,
    payload: ReviewDecisionReassignRequest,
    db: Session = Depends(get_db),
) -> TaskRead:
    review = _get_review_or_404(db, review_id)
    task = db.get(TaskORM, review.task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    agent_role = _get_enabled_role(db, payload.agent_role_id)
    task = _reassign_task(
        db,
        review=review,
        task=task,
        reviewer=payload.reviewer,
        review_comment=payload.review_comment,
        agent_role=agent_role,
    )
    db.commit()
    db.refresh(task)
    return TaskRead.model_validate(task)
