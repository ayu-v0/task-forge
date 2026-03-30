from __future__ import annotations

from sqlalchemy.orm import Session

from src.apps.worker.loop import run_worker_loop
from src.apps.worker.registry import build_default_registry
from src.apps.worker.settings import settings
from src.packages.core.db.session import create_engine_from_env


def main() -> int:
    engine = create_engine_from_env()
    registry = build_default_registry()
    return run_worker_loop(
        lambda: Session(engine),
        registry,
        max_concurrency=settings.worker_max_concurrency,
        poll_interval_seconds=settings.worker_poll_interval_seconds,
    )


if __name__ == "__main__":
    main()
