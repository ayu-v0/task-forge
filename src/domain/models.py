from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class TaskBatchStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    IN_PROGRESS = "in_progress"
    WAITING_REVIEW = "waiting_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class TaskStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED = "blocked"
    NEEDS_REVIEW = "needs_review"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class AssignmentStatus(str, Enum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    FULFILLED = "fulfilled"


class ExecutionRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    WAIVED = "waived"


class DomainModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        use_enum_values=True,
    )


class TaskBatch(DomainModel):
    id: str = Field(default_factory=lambda: _id("batch"))
    title: str
    description: str | None = None
    created_by: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    status: TaskBatchStatus = TaskBatchStatus.DRAFT
    total_tasks: int = Field(ge=0, default=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Task(DomainModel):
    id: str = Field(default_factory=lambda: _id("task"))
    batch_id: str
    title: str
    description: str | None = None
    task_type: str
    priority: TaskPriority = TaskPriority.MEDIUM
    status: TaskStatus = TaskStatus.PENDING
    input_payload: dict[str, Any] = Field(default_factory=dict)
    expected_output_schema: dict[str, Any] = Field(default_factory=dict)
    assigned_agent_role: str | None = None
    dependency_ids: list[str] = Field(default_factory=list)
    retry_count: int = Field(ge=0, default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AgentRole(DomainModel):
    id: str = Field(default_factory=lambda: _id("role"))
    role_name: str
    description: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(gt=0, default=300)
    max_retries: int = Field(ge=0, default=0)
    enabled: bool = True
    version: str = "1.0.0"


class Assignment(DomainModel):
    id: str = Field(default_factory=lambda: _id("assign"))
    task_id: str
    agent_role_id: str
    routing_reason: str | None = None
    assigned_at: datetime = Field(default_factory=datetime.utcnow)
    assignment_status: AssignmentStatus = AssignmentStatus.PROPOSED


class ExecutionRun(DomainModel):
    id: str = Field(default_factory=lambda: _id("run"))
    task_id: str
    agent_role_id: str
    run_status: ExecutionRunStatus = ExecutionRunStatus.QUEUED
    started_at: datetime | None = None
    finished_at: datetime | None = None
    logs: list[str] = Field(default_factory=list)
    input_snapshot: dict[str, Any] = Field(default_factory=dict)
    output_snapshot: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    token_usage: dict[str, int] = Field(default_factory=dict)
    latency_ms: int | None = Field(default=None, ge=0)


class ReviewCheckpoint(DomainModel):
    id: str = Field(default_factory=lambda: _id("review"))
    task_id: str
    reason: str
    review_status: ReviewStatus = ReviewStatus.PENDING
    reviewer: str | None = None
    review_comment: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: datetime | None = None
