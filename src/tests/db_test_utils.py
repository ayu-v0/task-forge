from __future__ import annotations

import json
import os
from typing import Any

from sqlalchemy.engine import Engine

from src.packages.core.db.config import get_database_url, load_database_config
from src.packages.core.db.session import create_engine_from_env


def database_url() -> str:
    return os.getenv("DATABASE_URL") or get_database_url()


def create_test_engine() -> Engine:
    return create_engine_from_env()


def decode_json_value(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value
