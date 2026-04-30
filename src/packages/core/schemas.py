from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from src.domain.models import (
    AssignmentStatus,
    ExecutionRunStatus,
    ReviewReasonCategory,
    ReviewStatus,
    ReviewTimeoutPolicy,
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


class BudgetReportRead(SchemaModel):
    model_context_limit: int = Field(ge=0, default=0)
    system_prompt_tokens: int = Field(ge=0, default=0)
    task_input_tokens: int = Field(ge=0, default=0)
    dependency_summary_tokens: int = Field(ge=0, default=0)
    global_background_tokens: int = Field(ge=0, default=0)
    result_summary_tokens: int = Field(ge=0, default=0)
    validation_rule_tokens: int = Field(ge=0, default=0)
    history_background_tokens: int = Field(ge=0, default=0)
    estimated_input_tokens: int = Field(ge=0, default=0)
    initial_estimated_input_tokens: int = Field(ge=0, default=0)
    reserved_output_tokens: int = Field(ge=0, default=0)
    safe_budget: int = Field(ge=0, default=0)
    overflow_risk: bool = False
    initial_overflow_risk: bool = False
    trim_applied: bool = False
    trim_steps: list[str] = Field(default_factory=list)
    degradation_mode: str = "full_context"
    budget_policy: dict[str, Any] = Field(default_factory=dict)


class PromptBudgetPolicyRead(SchemaModel):
    template_name: str = "default"
    model_context_limit: int = Field(ge=1, default=128000)
    max_global_background_tokens: int = Field(ge=0, default=256)
    max_task_input_tokens: int = Field(ge=0, default=4096)
    max_dependency_summary_tokens: int = Field(ge=0, default=1024)
    max_result_summary_tokens: int = Field(ge=0, default=512)
    max_validation_rule_tokens: int = Field(ge=0, default=512)
    max_history_background_tokens: int = Field(ge=0, default=256)
    reserved_output_tokens: int = Field(ge=1, default=256)


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
    tasks: list[TaskBatchTaskCreate] = Field(min_length=1, max_length=20)


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


class TaskNormalizationRead(SchemaModel):
    client_task_id: str
    effective_client_task_id: str
    action: str
    is_ambiguous: bool = False
    missing_fields_filled: list[str] = Field(default_factory=list)
    inferred_dependency_client_task_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    recognized_intent: dict[str, Any] | None = None


class TaskBatchSubmitResponse(SchemaModel):
    batch_id: str
    original_task_count: int
    normalized_task_count: int
    tasks: list[TaskBatchSubmitTaskRead]
    normalization: list[TaskNormalizationRead] = Field(default_factory=list)


class BatchCountsRead(SchemaModel):
    pending_count: int = 0
    queued_count: int = 0
    running_count: int = 0
    blocked_count: int = 0
    needs_review_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    cancelled_count: int = 0


class BatchProgressRead(SchemaModel):
    completed_count: int
    total_tasks: int
    progress_percent: float


class BatchTaskResultRead(SchemaModel):
    task_id: str
    title: str
    task_type: str
    status: TaskStatus
    dependency_ids: list[str] = Field(default_factory=list)
    assigned_agent_role: str | None = None
    latest_run_id: str | None = None
    latest_run_status: ExecutionRunStatus | None = None
    output_snapshot: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    cancel_reason: str | None = None
    error_category: str | None = None
    artifact_count: int = 0


class BatchArtifactRead(SchemaModel):
    artifact_id: str
    task_id: str | None = None
    run_id: str | None = None
    artifact_type: str
    uri: str
    content_type: str | None = None
    raw_content: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    structured_output: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("metadata_json", "metadata"),
    )
    schema_version: str = "artifact.v1"
    created_at: datetime


class FailureCategorySummaryRead(SchemaModel):
    category: str
    count: int
    task_ids: list[str] = Field(default_factory=list)
    sample_messages: list[str] = Field(default_factory=list)


class TaskBatchSummaryRead(SchemaModel):
    batch: TaskBatchRead
    derived_status: str
    counts: BatchCountsRead
    progress: BatchProgressRead
    tasks: list[BatchTaskResultRead]
    artifacts: list[BatchArtifactRead]
    failure_categories: list[FailureCategorySummaryRead] = Field(default_factory=list)


class TaskBatchListItemRead(SchemaModel):
    batch_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    total_tasks: int
    derived_status: str
    success_rate: float
    completed_count: int
    success_count: int
    failed_count: int
    cancelled_count: int


class TaskBatchListResponse(SchemaModel):
    items: list[TaskBatchListItemRead] = Field(default_factory=list)


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
    task_summary: dict[str, Any] = Field(default_factory=dict)


class TaskEventRead(SchemaModel):
    id: str
    task_id: str
    event_type: str
    event_status: str | None = None
    message: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class TaskStatusHistoryItemRead(SchemaModel):
    task_id: str
    old_status: str | None = None
    new_status: str
    timestamp: datetime
    reason: str | None = None
    actor: str | None = None


class TimelineItemRead(SchemaModel):
    timestamp: datetime
    stage: str
    title: str
    detail: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    status: str | None = None
    actor: str | None = None


class TaskTimelineRead(SchemaModel):
    task_id: str
    batch_id: str
    items: list[TimelineItemRead] = Field(default_factory=list)


class BatchTimelineRead(SchemaModel):
    batch_id: str
    title: str
    items: list[TimelineItemRead] = Field(default_factory=list)


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
    prompt_budget_policy: PromptBudgetPolicyRead = Field(default_factory=PromptBudgetPolicyRead)
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
    prompt_budget_policy: PromptBudgetPolicyRead | None = None
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
    prompt_budget_policy: PromptBudgetPolicyRead = Field(default_factory=PromptBudgetPolicyRead)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int
    max_retries: int
    enabled: bool
    version: str


class AgentRegistryListItemRead(SchemaModel):
    id: str
    role_name: str
    description: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    capability_declaration: AgentCapabilityDeclaration
    prompt_budget_policy: PromptBudgetPolicyRead = Field(default_factory=PromptBudgetPolicyRead)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    enabled: bool
    version: str
    total_runs: int = 0
    success_runs: int = 0
    success_rate: float | None = None
    average_latency_ms: float | None = None
    retry_rate: float | None = None
    average_prompt_tokens: float = 0
    average_completion_tokens: float = 0
    average_total_tokens: float = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    average_cost_estimate: float = 0
    total_cost_estimate: float = 0


class AgentRegistryDiagnosisRead(SchemaModel):
    task_type: str
    status: str
    message: str
    matching_enabled_roles: list[str] = Field(default_factory=list)
    matching_disabled_roles: list[str] = Field(default_factory=list)


class AgentRegistryResponse(SchemaModel):
    items: list[AgentRegistryListItemRead] = Field(default_factory=list)
    diagnosis: AgentRegistryDiagnosisRead | None = None


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
    budget_report: BudgetReportRead = Field(default_factory=BudgetReportRead)
    latency_ms: int | None = Field(default=None, ge=0)


class ExecutionRunRead(ExecutionRunCreate):
    id: str
    result_summary: dict[str, Any] = Field(default_factory=dict)


class RunDetailTaskRead(SchemaModel):
    task_id: str
    title: str
    task_type: str
    status: TaskStatus
    assigned_agent_role: str | None = None
    retry_count: int
    batch_id: str


class RunRoutingRead(SchemaModel):
    routing_reason: str | None = None
    agent_role_id: str | None = None
    agent_role_name: str | None = None


class RunRoutingSnapshotRead(SchemaModel):
    task_id: str
    run_id: str
    assignment_id: str | None = None
    agent_role_id: str | None = None
    agent_role_name: str | None = None
    routing_reason: str | None = None
    task_type: str | None = None
    input_snapshot: dict[str, Any] = Field(default_factory=dict)
    expected_output_schema: dict[str, Any] = Field(default_factory=dict)
    dependency_ids: list[str] = Field(default_factory=list)
    task_summary: dict[str, Any] = Field(default_factory=dict)
    dependency_summaries: list[dict[str, Any]] = Field(default_factory=list)


class RunRetryHistoryItemRead(SchemaModel):
    run_id: str
    run_status: ExecutionRunStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    error_message: str | None = None
    is_current: bool = False


class RunDetailRead(SchemaModel):
    run: ExecutionRunRead
    task: RunDetailTaskRead
    routing: RunRoutingRead
    retry_history: list[RunRetryHistoryItemRead] = Field(default_factory=list)
    events: list[TaskEventRead] = Field(default_factory=list)
    cost_estimate: float = 0
    error_category: str | None = None
    result_summary: dict[str, Any] = Field(default_factory=dict)


class RunReplayRead(SchemaModel):
    run: ExecutionRunRead
    task: RunDetailTaskRead
    routing_snapshot: RunRoutingSnapshotRead | None = None
    status_history: list[TaskStatusHistoryItemRead] = Field(default_factory=list)
    timeline: TaskTimelineRead
    events: list[TaskEventRead] = Field(default_factory=list)
    replay_ready: bool = False


class BatchReplayItemRead(SchemaModel):
    task_id: str
    title: str
    task_type: str
    status: str
    routing_snapshot: RunRoutingSnapshotRead | None = None
    latest_run: ExecutionRunRead | None = None
    timeline: TaskTimelineRead


class BatchReplayRead(SchemaModel):
    batch: TaskBatchRead
    derived_status: str
    items: list[BatchReplayItemRead] = Field(default_factory=list)


class ReviewCheckpointCreate(SchemaModel):
    task_id: str
    reason: str
    reason_category: ReviewReasonCategory = ReviewReasonCategory.OTHER
    timeout_policy: ReviewTimeoutPolicy = ReviewTimeoutPolicy.FAIL_CLOSED
    review_status: ReviewStatus = ReviewStatus.PENDING
    reviewer: str | None = None
    review_comment: str | None = None
    deadline_at: datetime | None = None


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


class ReviewDecisionReassignRequest(SchemaModel):
    reviewer: str = Field(min_length=1)
    review_comment: str | None = None
    agent_role_id: str = Field(min_length=1)


class BulkReviewApproveRequest(SchemaModel):
    review_ids: list[str] = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    review_comment: str | None = None
    agent_role_id: str = Field(min_length=1)


class BulkReviewRejectRequest(SchemaModel):
    review_ids: list[str] = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    review_comment: str = Field(min_length=1)


class BulkReviewReassignRequest(SchemaModel):
    review_ids: list[str] = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    review_comment: str | None = None
    agent_role_id: str = Field(min_length=1)


class BulkReviewItemResult(SchemaModel):
    review_id: str
    ok: bool
    task_id: str | None = None
    status: str | None = None
    assigned_agent_role: str | None = None
    detail: str | None = None


class BulkReviewDecisionResponse(SchemaModel):
    items: list[BulkReviewItemResult] = Field(default_factory=list)


class ReviewTimeoutProcessRequest(SchemaModel):
    limit: int = Field(default=100, ge=1, le=500)


class ReviewTimeoutProcessResponse(SchemaModel):
    processed_count: int = 0
    items: list[BulkReviewItemResult] = Field(default_factory=list)


class ArtifactCreate(SchemaModel):
    task_id: str | None = None
    run_id: str | None = None
    artifact_type: str
    uri: str
    content_type: str | None = None
    raw_content: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    structured_output: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("metadata_json", "metadata"),
    )
    schema_version: str = "artifact.v1"


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
