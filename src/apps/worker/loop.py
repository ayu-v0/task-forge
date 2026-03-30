from __future__ import annotations

import concurrent.futures
import time
from collections.abc import Callable

from sqlalchemy.orm import Session

from src.apps.worker.executor import claim_next_task, execute_task
from src.apps.worker.registry import AgentRegistry


SessionFactory = Callable[[], Session]


def _execute_claimed_task(
    session_factory: SessionFactory,
    registry: AgentRegistry,
    task,
    run,
    agent_role,
):
    with session_factory() as db:
        return execute_task(db, registry, task, run, agent_role)


def run_worker_loop(
    session_factory: SessionFactory,
    registry: AgentRegistry,
    *,
    max_concurrency: int = 4,
    poll_interval_seconds: float = 1.0,
    max_iterations: int | None = None,
) -> int:
    processed = 0
    iterations = 0
    active_futures: set[concurrent.futures.Future] = set()

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        while max_iterations is None or iterations < max_iterations:
            iterations += 1

            completed = {future for future in active_futures if future.done()}
            for future in completed:
                future.result()
                active_futures.remove(future)
                processed += 1

            available_slots = max_concurrency - len(active_futures)
            claimed_any = False
            for _ in range(max(0, available_slots)):
                with session_factory() as db:
                    claimed = claim_next_task(db)
                if claimed is None:
                    break

                task, run, agent_role = claimed
                future = executor.submit(
                    _execute_claimed_task,
                    session_factory,
                    registry,
                    task,
                    run,
                    agent_role,
                )
                active_futures.add(future)
                claimed_any = True

            if not active_futures and not claimed_any:
                time.sleep(poll_interval_seconds)
                continue

            if not claimed_any:
                time.sleep(poll_interval_seconds)

        for future in concurrent.futures.as_completed(active_futures):
            future.result()
            processed += 1

    return processed
