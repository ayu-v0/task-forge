from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SQLITE_URL = "sqlite:///data/task_forge.sqlite3"
CONFIG_FILE_NAME = "task_forge_config.json"


@dataclass(frozen=True)
class DatabaseConfig:
    driver: str
    url: str
    connect_args: dict[str, Any] = field(default_factory=dict)


def _load_database_url_from_dotenv() -> str | None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return None

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == "DATABASE_URL":
            return value.strip().strip('"').strip("'")
    return None


def _load_database_url_from_config_file() -> str | None:
    config_path = ROOT / CONFIG_FILE_NAME
    if not config_path.exists():
        return None

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    database = payload.get("database")
    if not isinstance(database, dict):
        return None
    url = database.get("url")
    if isinstance(url, str) and url.strip():
        return url.strip()
    driver = database.get("driver")
    if driver == "sqlite":
        return DEFAULT_SQLITE_URL
    return None


def _driver_from_url(url: str) -> str:
    normalized = url.lower()
    if normalized.startswith("sqlite"):
        return "sqlite"
    if normalized.startswith("postgresql") or normalized.startswith("postgres"):
        return "postgresql"
    return normalized.split(":", 1)[0]


def _ensure_sqlite_parent(url: str) -> None:
    if not url.startswith("sqlite:///") or url.startswith("sqlite:///:memory:"):
        return

    raw_path = url.removeprefix("sqlite:///")
    db_path = Path(raw_path)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)


def load_database_config() -> DatabaseConfig:
    url = (
        os.getenv("DATABASE_URL")
        or _load_database_url_from_dotenv()
        or _load_database_url_from_config_file()
        or DEFAULT_SQLITE_URL
    )
    driver = _driver_from_url(url)
    connect_args: dict[str, Any] = {}
    if driver == "sqlite":
        _ensure_sqlite_parent(url)
        connect_args["check_same_thread"] = False
    return DatabaseConfig(driver=driver, url=url, connect_args=connect_args)


def get_database_url() -> str:
    return load_database_config().url
