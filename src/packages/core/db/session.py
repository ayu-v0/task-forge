from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from .config import get_database_url, load_database_config


def _configure_sqlite_engine(engine: Engine) -> None:
    if engine.url.get_backend_name() != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def create_engine_from_env(echo: bool = False) -> Engine:
    database_config = load_database_config()
    engine = create_engine(database_config.url, echo=echo, connect_args=database_config.connect_args)
    _configure_sqlite_engine(engine)
    return engine
