from __future__ import annotations

from sqlalchemy.orm import Session

from src.packages.core.db import Base
from src.packages.core.db.config import DEFAULT_SQLITE_URL, get_database_url, load_database_config
from src.packages.core.db.models import AgentRoleORM, ExecutionRunORM, TaskBatchORM, TaskORM
from src.packages.core.db.session import create_engine_from_env


def test_database_config_defaults_to_sqlite(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    database_config = load_database_config()

    assert database_config.driver == "sqlite"
    assert database_config.url == DEFAULT_SQLITE_URL
    assert database_config.connect_args["check_same_thread"] is False
    assert get_database_url() == DEFAULT_SQLITE_URL


def test_database_config_uses_environment_url(monkeypatch) -> None:
    database_url = "postgresql+psycopg://postgres:postgres@localhost:5432/task_forge"
    monkeypatch.setenv("DATABASE_URL", database_url)

    database_config = load_database_config()

    assert database_config.driver == "postgresql"
    assert database_config.url == database_url
    assert database_config.connect_args == {}


def test_sqlite_persists_json_objects_and_lists(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "task_forge.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    engine = create_engine_from_env()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        role = AgentRoleORM(
            role_name="sqlite-json-role",
            description="SQLite JSON role",
            capabilities=["task:sqlite", "json:list"],
            input_schema={"properties": {"text": {"type": "string"}}},
            output_schema={"output_contract": {"type": "object"}},
        )
        session.add(role)
        session.flush()

        batch = TaskBatchORM(
            title="SQLite JSON batch",
            description="Verify JSON/list persistence",
            created_by="pytest",
            status="submitted",
            total_tasks=1,
            metadata_json={"suite": "db-config"},
        )
        session.add(batch)
        session.flush()

        task = TaskORM(
            batch_id=batch.id,
            title="SQLite JSON task",
            description="Verify JSON/list persistence",
            task_type="sqlite",
            priority="medium",
            status="queued",
            input_payload={"text": "hello", "nested": {"items": [1, 2]}},
            expected_output_schema={"type": "object"},
            assigned_agent_role=role.role_name,
            dependency_ids=["task_upstream"],
        )
        session.add(task)
        session.flush()

        session.add(
            ExecutionRunORM(
                task_id=task.id,
                agent_role_id=role.id,
                run_status="success",
                logs=["started", "finished"],
                input_snapshot=task.input_payload,
                output_snapshot={"ok": True},
                token_usage={"total_tokens": 10},
                budget_report={"overflow_risk": False},
            )
        )
        session.commit()

    with Session(engine) as session:
        role = session.query(AgentRoleORM).filter_by(role_name="sqlite-json-role").one()
        task = session.query(TaskORM).filter_by(title="SQLite JSON task").one()
        run = session.query(ExecutionRunORM).filter_by(task_id=task.id).one()

        assert role.capabilities == ["task:sqlite", "json:list"]
        assert task.dependency_ids == ["task_upstream"]
        assert task.input_payload["nested"]["items"] == [1, 2]
        assert run.logs == ["started", "finished"]
        assert run.output_snapshot == {"ok": True}
