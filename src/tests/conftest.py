from __future__ import annotations

import os
from pathlib import Path

import sqlalchemy

from src.packages.core.db import Base, create_engine_from_env
from src.packages.core.db import models  # noqa: F401
from src.packages.core.db.session import _configure_sqlite_engine


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEST_DATABASE_URL = "sqlite:///data/task_forge_test.sqlite3"
DISABLED_MODEL_CONFIG_PATH = ROOT / "data" / "disabled_model_config.json"


if not os.getenv("DATABASE_URL"):
    os.environ["DATABASE_URL"] = DEFAULT_TEST_DATABASE_URL
if not os.getenv("TASK_FORGE_MODEL_CONFIG"):
    os.environ["TASK_FORGE_MODEL_CONFIG"] = str(DISABLED_MODEL_CONFIG_PATH)


_original_create_engine = sqlalchemy.create_engine


def _create_engine_with_sqlite_defaults(*args, **kwargs):
    url = str(args[0] if args else kwargs.get("url", ""))
    if url.startswith("sqlite"):
        connect_args = dict(kwargs.pop("connect_args", {}) or {})
        connect_args.setdefault("check_same_thread", False)
        kwargs["connect_args"] = connect_args
    engine = _original_create_engine(*args, **kwargs)
    _configure_sqlite_engine(engine)
    return engine


sqlalchemy.create_engine = _create_engine_with_sqlite_defaults


def _ensure_test_schema() -> None:
    engine = create_engine_from_env()
    Base.metadata.create_all(engine)


_ensure_test_schema()


def pytest_sessionstart(session) -> None:
    _ensure_test_schema()
