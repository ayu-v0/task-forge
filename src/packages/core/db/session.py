from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def _load_database_url_from_dotenv() -> str | None:
    env_path = Path(__file__).resolve().parents[4] / ".env"
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


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL") or _load_database_url_from_dotenv()
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")
    return database_url


def create_engine_from_env(echo: bool = False) -> Engine:
    return create_engine(get_database_url(), echo=echo)
