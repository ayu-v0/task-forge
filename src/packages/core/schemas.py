from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from src.domain.models import (
    AssignmentStatus,
    ExecutionRunStatus,
    ReviewStatus,
    TaskBatchStatus,
    TaskPriority,
    TaskStatus,
)


class SchemaModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        use_enum_values=True,
    )


class TaskBatchCreate(SchemaModel):
    title: str
    description: str | None = None
    created_by: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskBatchRead(TaskBatchCreate):
    id: str
    created_at: datetime
    status: TaskBatchStatus
    total_tasks: int
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("metadata_json", "metadata"),
    )


class TaskBatchTaskCreate(SchemaModel):
    client_task_id: str
    title: str
    description: str | None = None
    task_type: str
    priority: TaskPriority = TaskPriority.MEDIUM
    input_payload: dict[str, Any] = Field(default_factory=dict)
    expected_output_schema: dict[str, Any] = Field(default_factory=dict)
    dependency_client_task_ids: list[str] = Field(default_factory=list)


class TaskBatchSubmitRequest(TaskBatchCreate):
    tasks: list[TaskBatchTaskCreate] = Field(min_length=3, max_length=20)


class TaskBatchSubmitTaskRead(SchemaModel):
    task_id: str
    client_task_id: str
    title: str
    status: TaskStatus
    dependency_ids: list[str] = Field(default_factory=list)
    assigned_agent_role: str | None = None
    routing_reason: str | None = None
    auto_execute: bool = False
    needs_review: bool = False


class TaskBatchSubmitResponse(SchemaModel):
    batch_id: str
    tasks: list[TaskBatchSubmitTaskRead]


class TaskCreate(SchemaModel):
    batch_id: str
    title: str
    description: str | None = None
    task_type: str
    priority: TaskPriority = TaskPriority.MEDIUM
    input_payload: dict[str, Any] = Field(default_factory=dict)
    expected_output_schema: dict[str, Any] = Field(default_factory=dict)
    assigned_agent_role: str | None = None
    dependency_ids: list[str] = Field(default_factory=list)


class TaskRead(TaskCreate):
    id: str
    status: TaskStatus
    retry_count: int
    cancellation_requested: bool = False
    cancellation_requested_at: datetime | None = None
    cancellation_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskEventRead(SchemaModel):
    id: str
    task_id: str
    event_type: str
    event_status: str | None = None
    message: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AgentRoleCreate(SchemaModel):
    role_name: str
    description: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(gt=0, default=300)
    max_retries: int = Field(ge=0, default=0)
    enabled: bool = True
    version: str = "1.0.0"


class AgentRoleRead(AgentRoleCreate):
    id: str


class AgentCapabilityDeclaration(SchemaModel):
    supported_task_types: list[str] = Field(default_factory=list)
    input_requirements: dict[str, Any] = Field(default_factory=dict)
    output_contract: dict[str, Any] = Field(default_factory=dict)
    supports_concurrency: bool = False
    allows_auto_retry: bool = False


class AgentRoleRegisterRequest(SchemaModel):
    role_name: str
    description: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    capability_declaration: AgentCapabilityDeclaration = Field(default_factory=AgentCapabilityDeclaration)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(gt=0, default=300)
    max_retries: int = Field(ge=0, default=0)
    enabled: bool = True
    version: str = "1.0.0"


class AgentRoleUpdateRequest(SchemaModel):
    description: str | None = None
    capabilities: list[str] | None = None
    capability_declaration: AgentCapabilityDeclaration | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    timeout_seconds: int | None = Field(default=None, gt=0)
    max_retries: int | None = Field(default=None, ge=0)
    enabled: bool | None = None
    version: str | None = None


class AgentRoleDetailRead(SchemaModel):
    id: str
    role_name: str
    description: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    capability_declaration: AgentCapabilityDeclaration
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int
    max_retries: int
    enabled: bool
    version: str


class AssignmentCreate(SchemaModel):
    task_id: str
    agent_role_id: str
    routing_reason: str | None = None
    assignment_status: AssignmentStatus = AssignmentStatus.PROPOSED


class AssignmentRead(AssignmentCreate):
    id: str
    assigned_at: datetime


class ExecutionRunCreate(SchemaModel):
    task_id: str
    agent_role_id: str
    run_status: ExecutionRunStatus = ExecutionRunStatus.QUEUED
    started_at: datetime | None = None
    finished_at: datetime | None = None
    logs: list[str] = Field(default_factory=list)
    input_snapshot: dict[str, Any] = Field(default_factory=dict)
    output_snapshot: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    cancelled_at: datetime | None = None
    cancel_reason: str | None = None
    token_usage: dict[str, int] = Field(default_factory=dict)
    latency_ms: int | None = Field(default=None, ge=0)


class ExecutionRunRead(ExecutionRunCreate):
    id: str


class ReviewCheckpointCreate(SchemaModel):
    task_id: str
    reason: str
    review_status: ReviewStatus = ReviewStatus.PENDING
    reviewer: str | None = None
    review_comment: str | None = None


class ReviewCheckpointRead(ReviewCheckpointCreate):
    id: str
    created_at: datetime
    resolved_at: datetime | None = None


class ReviewDecisionApproveRequest(SchemaModel):
    reviewer: str = Field(min_length=1)
    review_comment: str | None = None
    agent_role_id: str = Field(min_length=1)


class ReviewDecisionRejectRequest(SchemaModel):
    reviewer: str = Field(min_length=1)
    review_comment: str = Field(min_length=1)


class ArtifactCreate(SchemaModel):
    task_id: str | None = None
    run_id: str | None = None
    artifact_type: str
    uri: str
    content_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactRead(ArtifactCreate):
    id: str
    created_at: datetime


class EventLogCreate(SchemaModel):
    batch_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    event_type: str
    event_status: str | None = None
    message: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EventLogRead(EventLogCreate):
    id: str
    created_at: datetime


class TaskCancelRequest(SchemaModel):
    reason: str = Field(min_length=1)
