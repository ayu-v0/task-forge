from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from src.apps.worker.registry import get_worker_agent
from src.packages.core.db.models import (
    AgentRoleORM,
    AssignmentORM,
    EventLogORM,
    ExecutionRunORM,
    TaskORM,
)
from src.packages.core.task_state_machine import transition_task_status


class WorkerService:
    def __init__(self, db: Session):
        self.db = db

    def _emit_execution_event(
        self,
        *,
        event_type: str,
        task: TaskORM,
        run: ExecutionRunORM,
        agent_role: AgentRoleORM,
        message: str,
    ) -> None:
        self.db.add(
            EventLogORM(
                batch_id=task.batch_id,
                task_id=task.id,
                run_id=run.id,
                event_type=event_type,
                event_status=run.run_status,
                message=message,
                payload={
                    "task_id": task.id,
                    "run_id": run.id,
                    "agent_role_id": agent_role.id,
                    "role_name": agent_role.role_name,
                },
            )
        )

    def _active_assignment_query(self, task_id: str) -> Select[tuple[AssignmentORM]]:
        return (
            select(AssignmentORM)
            .where(
                AssignmentORM.task_id == task_id,
                AssignmentORM.assignment_status == "active",
            )
            .order_by(AssignmentORM.assigned_at.desc())
            .limit(1)
        )

    def claim_next_task(self) -> TaskORM | None:
        task = self.db.scalar(
            select(TaskORM)
            .where(TaskORM.status == "queued")
            .order_by(TaskORM.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if task is None:
            return None

        transition_task_status(
            self.db,
            task,
            to_status="running",
            reason="Worker claimed queued task",
            source="worker",
        )
        self.db.flush()
        return task

    def execute_task(self, task: TaskORM) -> ExecutionRunORM:
        assignment = self.db.scalar(self._active_assignment_query(task.id))
        if assignment is None:
            raise RuntimeError(f"No active assignment found for task {task.id}")

        agent_role = self.db.get(AgentRoleORM, assignment.agent_role_id)
        if agent_role is None:
            raise RuntimeError(f"Assigned agent role {assignment.agent_role_id} not found")

        started_at = datetime.now(timezone.utc)

        run = ExecutionRunORM(
            task_id=task.id,
            agent_role_id=agent_role.id,
            run_status="running",
            started_at=started_at,
            logs=[f"Execution started for role {agent_role.role_name}"],
            input_snapshot=task.input_payload,
            output_snapshot={},
            error_message=None,
            token_usage={},
            latency_ms=None,
        )
        self.db.add(run)
        self.db.flush()

        self._emit_execution_event(
            event_type="task_execution_started",
            task=task,
            run=run,
            agent_role=agent_role,
            message="Worker execution started",
        )

        try:
            agent = get_worker_agent(agent_role.role_name)
            result = agent.run(
                task,
                {
                    "agent_role_id": agent_role.id,
                    "role_name": agent_role.role_name,
                    "assignment_id": assignment.id,
                },
            )
            finished_at = datetime.now(timezone.utc)
            run.run_status = "succeeded"
            run.finished_at = finished_at
            run.output_snapshot = result
            run.logs = [*run.logs, "Execution completed successfully"]
            run.latency_ms = max(int((finished_at - started_at).total_seconds() * 1000), 0)
            assignment.assignment_status = "fulfilled"
            transition_task_status(
                self.db,
                task,
                to_status="success",
                reason="Worker execution completed successfully",
                source="worker",
                run_id=run.id,
            )
            self._emit_execution_event(
                event_type="task_execution_finished",
                task=task,
                run=run,
                agent_role=agent_role,
                message="Worker execution finished",
            )
            self.db.flush()
            return run
        except Exception as exc:
            finished_at = datetime.now(timezone.utc)
            run.run_status = "failed"
            run.finished_at = finished_at
            run.error_message = str(exc)
            run.logs = [*run.logs, f"Execution failed: {exc}"]
            run.latency_ms = max(int((finished_at - started_at).total_seconds() * 1000), 0)
            transition_task_status(
                self.db,
                task,
                to_status="failed",
                reason=f"Worker execution failed: {exc}",
                source="worker",
                run_id=run.id,
            )
            self._emit_execution_event(
                event_type="task_execution_failed",
                task=task,
                run=run,
                agent_role=agent_role,
                message=str(exc),
            )
            self.db.flush()
            return run

    def run_once(self) -> ExecutionRunORM | None:
        with self.db.begin():
            task = self.claim_next_task()
            if task is None:
                return None
            return self.execute_task(task)
