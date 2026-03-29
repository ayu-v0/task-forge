from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.apps.api.settings import settings


engine = create_engine(settings.database_url)


def get_db() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
