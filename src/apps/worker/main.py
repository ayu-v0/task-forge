from __future__ import annotations

from sqlalchemy.orm import Session

from src.apps.worker.loop import run_worker_loop
from src.apps.worker.registry import build_default_registry
from src.packages.core.db.session import create_engine_from_env


def main() -> int:
    engine = create_engine_from_env()
    registry = build_default_registry()
    with Session(engine) as db:
        return run_worker_loop(db, registry)


if __name__ == "__main__":
    main()
