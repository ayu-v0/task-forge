from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.packages.core.db.models import AgentRoleORM, AssignmentORM, ExecutionRunORM, TaskBatchORM, TaskORM  # noqa: E402


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def main() -> None:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")

    engine = create_engine(database_url)

    with Session(engine) as session:
        role_name = f"verifier_{int(datetime.now(timezone.utc).timestamp())}"
        role = AgentRoleORM(
            role_name=role_name,
            description="Local verification role",
            capabilities=["verify-db-flow"],
            version="1.0.0",
        )
        session.add(role)
        session.flush()

        batch = TaskBatchORM(
            title="Verification Batch",
            description="Create one task and validate status tracking",
            created_by="codex",
            status="in_progress",
            total_tasks=1,
        )
        session.add(batch)
        session.flush()

        task = TaskORM(
            batch_id=batch.id,
            title="Verification Task",
            description="Verify DB flow",
            task_type="verification",
            priority="medium",
            status="running",
            input_payload={"source": "verify_db_flow.py"},
            expected_output_schema={"type": "object"},
            assigned_agent_role=role.role_name,
            dependency_ids=[],
        )
        session.add(task)
        session.flush()

        assignment = AssignmentORM(
            task_id=task.id,
            agent_role_id=role.id,
            routing_reason="verification routing",
            assignment_status="active",
        )
        session.add(assignment)
        session.flush()

        run = ExecutionRunORM(
            task_id=task.id,
            agent_role_id=role.id,
            run_status="succeeded",
            started_at=utcnow(),
            finished_at=utcnow(),
            logs=["verification run"],
            input_snapshot={"task_id": task.id},
            output_snapshot={"verified": True},
            latency_ms=10,
        )
        session.add(run)

        task.status = "completed"
        task.updated_at = utcnow()
        batch.status = "completed"

        session.commit()

        created_batch = session.scalar(select(TaskBatchORM).where(TaskBatchORM.id == batch.id))
        created_task = session.scalar(select(TaskORM).where(TaskORM.id == task.id))
        task_execution = session.execute(
            select(TaskORM.title, AgentRoleORM.role_name, ExecutionRunORM.run_status)
            .join(ExecutionRunORM, ExecutionRunORM.task_id == TaskORM.id)
            .join(AgentRoleORM, AgentRoleORM.id == ExecutionRunORM.agent_role_id)
            .where(TaskORM.id == task.id)
        ).one()

        print(f"batch_created={created_batch.id}")
        print(f"task_status={created_task.status}")
        print(f"task_execution=task:{task_execution.title}, role:{task_execution.role_name}, run_status:{task_execution.run_status}")


if __name__ == "__main__":
    main()
