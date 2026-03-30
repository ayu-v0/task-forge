from __future__ import annotations

from fastapi import FastAPI

from src.apps.api.bootstrap import ensure_builtin_agent_roles
from src.apps.api.routers import (
    agents_router,
    health_router,
    reviews_router,
    runs_router,
    task_batches_router,
    tasks_router,
)
from src.apps.api.settings import settings

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
)

app.include_router(health_router)
app.include_router(task_batches_router)
app.include_router(tasks_router)
app.include_router(agents_router)
app.include_router(runs_router)
app.include_router(reviews_router)


@app.on_event("startup")
def bootstrap_defaults() -> None:
    ensure_builtin_agent_roles()
