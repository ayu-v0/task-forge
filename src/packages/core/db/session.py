from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")
    return database_url


def create_engine_from_env(echo: bool = False) -> Engine:
    return create_engine(get_database_url(), echo=echo)
