from __future__ import annotations

from collections import deque

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.apps.api.deps import get_db
from src.packages.core.db.models import TaskBatchORM, TaskORM
from src.packages.core.schemas import (
    TaskBatchRead,
    TaskBatchSubmitRequest,
    TaskBatchSubmitResponse,
    TaskBatchSubmitTaskRead,
)

router = APIRouter(prefix="/task-batches", tags=["task-batches"])


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

        return TaskBatchSubmitResponse(
            batch_id=task_batch.id,
            tasks=[
                TaskBatchSubmitTaskRead(
                    task_id=task_mapping[submitted_task.client_task_id].id,
                    client_task_id=submitted_task.client_task_id,
                    title=submitted_task.title,
                    status=task_mapping[submitted_task.client_task_id].status,
                    dependency_ids=task_mapping[submitted_task.client_task_id].dependency_ids,
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
