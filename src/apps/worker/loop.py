from __future__ import annotations

import time

from sqlalchemy.orm import Session

from src.apps.worker.executor import run_next_task
from src.apps.worker.registry import AgentRegistry


def run_worker_loop(
    db: Session,
    registry: AgentRegistry,
    *,
    poll_interval_seconds: float = 1.0,
    max_iterations: int | None = None,
) -> int:
    processed = 0
    iterations = 0

    while max_iterations is None or iterations < max_iterations:
        iterations += 1
        run = run_next_task(db, registry)
        if run is None:
            time.sleep(poll_interval_seconds)
            continue
        processed += 1

    return processed
