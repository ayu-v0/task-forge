from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TaskBatchORM(Base):
    __tablename__ = "task_batches"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _id("batch"))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    total_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)

    tasks: Mapped[list["TaskORM"]] = relationship(back_populates="batch", cascade="all, delete-orphan")
    event_logs: Mapped[list["EventLogORM"]] = relationship(back_populates="batch")


class TaskORM(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _id("task"))
    batch_id: Mapped[str] = mapped_column(ForeignKey("task_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="medium", index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    input_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    expected_output_schema: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    assigned_agent_role: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dependency_ids: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False, default=list)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    batch: Mapped["TaskBatchORM"] = relationship(back_populates="tasks")
    assignments: Mapped[list["AssignmentORM"]] = relationship(back_populates="task", cascade="all, delete-orphan")
    execution_runs: Mapped[list["ExecutionRunORM"]] = relationship(back_populates="task", cascade="all, delete-orphan")
    review_checkpoints: Mapped[list["ReviewCheckpointORM"]] = relationship(back_populates="task", cascade="all, delete-orphan")
    artifacts: Mapped[list["ArtifactORM"]] = relationship(back_populates="task")
    event_logs: Mapped[list["EventLogORM"]] = relationship(back_populates="task")


class AgentRoleORM(Base):
    __tablename__ = "agent_roles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _id("role"))
    role_name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(String(128)), nullable=False, default=list)
    input_schema: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    output_schema: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0.0")

    assignments: Mapped[list["AssignmentORM"]] = relationship(back_populates="agent_role")
    execution_runs: Mapped[list["ExecutionRunORM"]] = relationship(back_populates="agent_role")


class AssignmentORM(Base):
    __tablename__ = "assignments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _id("assign"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_role_id: Mapped[str] = mapped_column(ForeignKey("agent_roles.id", ondelete="RESTRICT"), nullable=False, index=True)
    routing_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    assignment_status: Mapped[str] = mapped_column(String(32), nullable=False, default="proposed", index=True)

    task: Mapped["TaskORM"] = relationship(back_populates="assignments")
    agent_role: Mapped["AgentRoleORM"] = relationship(back_populates="assignments")


class ExecutionRunORM(Base):
    __tablename__ = "execution_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _id("run"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_role_id: Mapped[str] = mapped_column(ForeignKey("agent_roles.id", ondelete="RESTRICT"), nullable=False, index=True)
    run_status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    logs: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    input_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    output_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_usage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    budget_report: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    task: Mapped["TaskORM"] = relationship(back_populates="execution_runs")
    agent_role: Mapped["AgentRoleORM"] = relationship(back_populates="execution_runs")
    artifacts: Mapped[list["ArtifactORM"]] = relationship(back_populates="run")
    event_logs: Mapped[list["EventLogORM"]] = relationship(back_populates="run")


class ReviewCheckpointORM(Base):
    __tablename__ = "review_checkpoints"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _id("review"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reason_category: Mapped[str] = mapped_column(String(64), nullable=False, default="other", index=True)
    timeout_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="fail_closed")
    review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    reviewer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    task: Mapped["TaskORM"] = relationship(back_populates="review_checkpoints")


class ArtifactORM(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _id("artifact"))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("execution_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    task: Mapped["TaskORM | None"] = relationship(back_populates="artifacts")
    run: Mapped["ExecutionRunORM | None"] = relationship(back_populates="artifacts")


class EventLogORM(Base):
    __tablename__ = "event_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: _id("event"))
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("task_batches.id", ondelete="CASCADE"), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("execution_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, index=True)

    batch: Mapped["TaskBatchORM | None"] = relationship(back_populates="event_logs")
    task: Mapped["TaskORM | None"] = relationship(back_populates="event_logs")
    run: Mapped["ExecutionRunORM | None"] = relationship(back_populates="event_logs")
