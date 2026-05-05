from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import Session

from src.packages.core.db.session import create_engine_from_env


engine = create_engine_from_env()


def get_db() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
