from __future__ import annotations

from collections import deque

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.apps.api.deps import get_db
from src.packages.core.db.models import (
    AgentRoleORM,
    AssignmentORM,
    ArtifactORM,
    EventLogORM,
    ExecutionRunORM,
    ReviewCheckpointORM,
    TaskBatchORM,
    TaskORM,
)
from src.packages.core.schemas import (
    BatchArtifactRead,
    BatchCountsRead,
    BatchProgressRead,
    BatchTaskResultRead,
    TaskBatchRead,
    TaskBatchSummaryRead,
    TaskBatchSubmitRequest,
    TaskBatchSubmitResponse,
    TaskBatchSubmitTaskRead,
)
from src.packages.core.task_state_machine import transition_task_status
from src.packages.router import route_task

router = APIRouter(prefix="/task-batches", tags=["task-batches"])


def _build_batch_counts(tasks: list[TaskORM]) -> BatchCountsRead:
    counts = {
        "pending_count": 0,
        "queued_count": 0,
        "running_count": 0,
        "blocked_count": 0,
        "needs_review_count": 0,
        "success_count": 0,
        "failed_count": 0,
        "cancelled_count": 0,
    }
    status_to_field = {
        "pending": "pending_count",
        "queued": "queued_count",
        "running": "running_count",
        "blocked": "blocked_count",
        "needs_review": "needs_review_count",
        "success": "success_count",
        "failed": "failed_count",
        "cancelled": "cancelled_count",
    }
    for task in tasks:
        field_name = status_to_field.get(task.status)
        if field_name is not None:
            counts[field_name] += 1
    return BatchCountsRead(**counts)


def _build_batch_progress(tasks: list[TaskORM]) -> BatchProgressRead:
    total_tasks = len(tasks)
    completed_count = sum(1 for task in tasks if task.status in {"success", "failed", "cancelled"})
    progress_percent = 0.0 if total_tasks == 0 else round((completed_count / total_tasks) * 100, 2)
    return BatchProgressRead(
        completed_count=completed_count,
        total_tasks=total_tasks,
        progress_percent=progress_percent,
    )


def _derive_batch_status(tasks: list[TaskORM]) -> str:
    if not tasks:
        return "pending"

    statuses = [task.status for task in tasks]
    unique_statuses = set(statuses)

    if unique_statuses == {"cancelled"}:
        return "cancelled"
    if "running" in unique_statuses:
        return "running"
    if "needs_review" in unique_statuses:
        return "needs_review"
    if unique_statuses & {"queued", "blocked", "pending"}:
        return "pending"
    if unique_statuses == {"success"}:
        return "success"
    if unique_statuses == {"failed"}:
        return "failed"
    if "success" in unique_statuses and "failed" in unique_statuses:
        return "partially_failed"
    if "cancelled" in unique_statuses:
        return "partially_failed"
    return "pending"


def _load_latest_runs(task_ids: list[str], db: Session) -> dict[str, ExecutionRunORM]:
    if not task_ids:
        return {}

    runs = db.scalars(
        select(ExecutionRunORM)
        .where(ExecutionRunORM.task_id.in_(task_ids))
        .order_by(ExecutionRunORM.started_at.desc(), ExecutionRunORM.id.desc())
    ).all()

    latest_runs: dict[str, ExecutionRunORM] = {}
    for run in runs:
        latest_runs.setdefault(run.task_id, run)
    return latest_runs


def _load_batch_artifacts(task_ids: list[str], db: Session) -> tuple[list[ArtifactORM], dict[str, int]]:
    if not task_ids:
        return [], {}

    artifacts = db.scalars(
        select(ArtifactORM)
        .where(ArtifactORM.task_id.in_(task_ids))
        .order_by(ArtifactORM.created_at.asc(), ArtifactORM.id.asc())
    ).all()

    artifact_counts: dict[str, int] = {}
    for artifact in artifacts:
        if artifact.task_id is not None:
            artifact_counts[artifact.task_id] = artifact_counts.get(artifact.task_id, 0) + 1
    return artifacts, artifact_counts


def _validate_unique_client_task_ids(payload: TaskBatchSubmitRequest) -> None:
    client_task_ids = [task.client_task_id for task in payload.tasks]
    duplicated = {task_id for task_id in client_task_ids if client_task_ids.count(task_id) > 1}
    if duplicated:
        duplicate_list = ", ".join(sorted(duplicated))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Duplicate client_task_id values: {duplicate_list}",
        )


def _validate_dependencies_exist(payload: TaskBatchSubmitRequest) -> None:
    task_ids = {task.client_task_id for task in payload.tasks}
    for task in payload.tasks:
        for dependency_id in task.dependency_client_task_ids:
            if dependency_id == task.client_task_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Task {task.client_task_id} cannot depend on itself",
                )
            if dependency_id not in task_ids:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Task {task.client_task_id} depends on unknown task {dependency_id}",
                )


def _detect_cycle(payload: TaskBatchSubmitRequest) -> None:
    dependency_map = {
        task.client_task_id: set(task.dependency_client_task_ids)
        for task in payload.tasks
    }
    indegree = {task_id: 0 for task_id in dependency_map}
    adjacency: dict[str, list[str]] = {task_id: [] for task_id in dependency_map}

    for task_id, dependency_ids in dependency_map.items():
        indegree[task_id] = len(dependency_ids)
        for dependency_id in dependency_ids:
            adjacency[dependency_id].append(task_id)

    queue = deque(task_id for task_id, degree in indegree.items() if degree == 0)
    visited = 0

    while queue:
        current = queue.popleft()
        visited += 1
        for downstream in adjacency[current]:
            indegree[downstream] -= 1
            if indegree[downstream] == 0:
                queue.append(downstream)

    if visited != len(payload.tasks):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Circular dependency detected in submitted tasks",
        )


@router.post("", response_model=TaskBatchSubmitResponse, status_code=status.HTTP_201_CREATED)
def create_task_batch(
    payload: TaskBatchSubmitRequest,
    db: Session = Depends(get_db),
) -> TaskBatchSubmitResponse:
    _validate_unique_client_task_ids(payload)
    _validate_dependencies_exist(payload)
    _detect_cycle(payload)

    task_mapping: dict[str, TaskORM] = {}
    routing_results: dict[str, dict[str, str | bool | None | list[str]]] = {}

    try:
        with db.begin():
            task_batch = TaskBatchORM(
                title=payload.title,
                description=payload.description,
                created_by=payload.created_by,
                status="draft",
                total_tasks=len(payload.tasks),
                metadata_json=payload.metadata,
            )
            db.add(task_batch)
            db.flush()

            for submitted_task in payload.tasks:
                task = TaskORM(
                    batch_id=task_batch.id,
                    title=submitted_task.title,
                    description=submitted_task.description,
                    task_type=submitted_task.task_type,
                    priority=submitted_task.priority,
                    status="pending",
                    input_payload=submitted_task.input_payload,
                    expected_output_schema=submitted_task.expected_output_schema,
                    assigned_agent_role=None,
                    dependency_ids=[],
                    retry_count=0,
                )
                db.add(task)
                db.flush()
                task_mapping[submitted_task.client_task_id] = task

            for submitted_task in payload.tasks:
                task = task_mapping[submitted_task.client_task_id]
                task.dependency_ids = [
                    task_mapping[dependency_id].id
                    for dependency_id in submitted_task.dependency_client_task_ids
                ]

            agent_roles = db.scalars(select(AgentRoleORM)).all()

            for submitted_task in payload.tasks:
                task = task_mapping[submitted_task.client_task_id]
                route_result = route_task(task, list(agent_roles))

                if route_result.needs_review:
                    task.assigned_agent_role = None
                    transition_task_status(
                        db,
                        task,
                        to_status="needs_review",
                        reason=route_result.routing_reason,
                        source="router",
                    )
                    review_checkpoint = ReviewCheckpointORM(
                        task_id=task.id,
                        reason=route_result.routing_reason,
                        review_status="pending",
                    )
                    db.add(review_checkpoint)
                    db.flush()
                    # This is an additive domain event for review workflow observability.
                    # The status transition event is still emitted by transition_task_status.
                    db.add(
                        EventLogORM(
                            batch_id=task.batch_id,
                            task_id=task.id,
                            event_type="review_checkpoint_created",
                            event_status="needs_review",
                            message=route_result.routing_reason,
                            payload={
                                "task_id": task.id,
                                "review_id": review_checkpoint.id,
                                "reason": route_result.routing_reason,
                                "source": "router",
                            },
                        )
                    )
                else:
                    task.assigned_agent_role = route_result.agent_role_name
                    assignment = AssignmentORM(
                        task_id=task.id,
                        agent_role_id=route_result.agent_role_id,
                        routing_reason=route_result.routing_reason,
                        assignment_status="active",
                    )
                    db.add(assignment)

                    if task.dependency_ids:
                        transition_task_status(
                            db,
                            task,
                            to_status="blocked",
                            reason="waiting for dependency tasks to complete",
                            source="router",
                        )
                    else:
                        transition_task_status(
                            db,
                            task,
                            to_status="queued",
                            reason=route_result.routing_reason,
                            source="router",
                        )

                routing_results[submitted_task.client_task_id] = {
                    "assigned_agent_role": route_result.agent_role_name,
                    "routing_reason": route_result.routing_reason,
                    "auto_execute": route_result.auto_execute,
                    "needs_review": route_result.needs_review,
                    "dependency_ids": task.dependency_ids,
                    "status": task.status,
                }

        return TaskBatchSubmitResponse(
            batch_id=task_batch.id,
            tasks=[
                TaskBatchSubmitTaskRead(
                    task_id=task_mapping[submitted_task.client_task_id].id,
                    client_task_id=submitted_task.client_task_id,
                    title=submitted_task.title,
                    status=routing_results[submitted_task.client_task_id]["status"],
                    dependency_ids=routing_results[submitted_task.client_task_id]["dependency_ids"],
                    assigned_agent_role=routing_results[submitted_task.client_task_id]["assigned_agent_role"],
                    routing_reason=routing_results[submitted_task.client_task_id]["routing_reason"],
                    auto_execute=routing_results[submitted_task.client_task_id]["auto_execute"],
                    needs_review=routing_results[submitted_task.client_task_id]["needs_review"],
                )
                for submitted_task in payload.tasks
            ],
        )
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise


@router.get("/{batch_id}", response_model=TaskBatchRead)
def get_task_batch(batch_id: str, db: Session = Depends(get_db)) -> TaskBatchRead:
    task_batch = db.get(TaskBatchORM, batch_id)
    if task_batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task batch not found")
    return TaskBatchRead.model_validate(task_batch)


@router.get("/{batch_id}/summary", response_model=TaskBatchSummaryRead)
def get_task_batch_summary(batch_id: str, db: Session = Depends(get_db)) -> TaskBatchSummaryRead:
    task_batch = db.get(TaskBatchORM, batch_id)
    if task_batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task batch not found")

    tasks = db.scalars(
        select(TaskORM)
        .where(TaskORM.batch_id == batch_id)
        .order_by(TaskORM.created_at.asc(), TaskORM.id.asc())
    ).all()
    task_ids = [task.id for task in tasks]
    latest_runs = _load_latest_runs(task_ids, db)
    artifacts, artifact_counts = _load_batch_artifacts(task_ids, db)
    counts = _build_batch_counts(tasks)
    progress = _build_batch_progress(tasks)
    derived_status = _derive_batch_status(tasks)

    task_results = [
        BatchTaskResultRead(
            task_id=task.id,
            title=task.title,
            task_type=task.task_type,
            status=task.status,
            assigned_agent_role=task.assigned_agent_role,
            latest_run_id=latest_runs.get(task.id).id if latest_runs.get(task.id) is not None else None,
            latest_run_status=latest_runs.get(task.id).run_status if latest_runs.get(task.id) is not None else None,
            output_snapshot=latest_runs.get(task.id).output_snapshot if latest_runs.get(task.id) is not None else {},
            error_message=latest_runs.get(task.id).error_message if latest_runs.get(task.id) is not None else None,
            cancel_reason=latest_runs.get(task.id).cancel_reason if latest_runs.get(task.id) is not None else None,
            artifact_count=artifact_counts.get(task.id, 0),
        )
        for task in tasks
    ]

    artifact_reads = [
        BatchArtifactRead(
            artifact_id=artifact.id,
            task_id=artifact.task_id,
            run_id=artifact.run_id,
            artifact_type=artifact.artifact_type,
            uri=artifact.uri,
            content_type=artifact.content_type,
            created_at=artifact.created_at,
        )
        for artifact in artifacts
    ]

    return TaskBatchSummaryRead(
        batch=TaskBatchRead.model_validate(task_batch),
        derived_status=derived_status,
        counts=counts,
        progress=progress,
        tasks=task_results,
        artifacts=artifact_reads,
    )
