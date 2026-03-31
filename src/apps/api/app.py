from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
app.mount("/console/assets", StaticFiles(directory=WEB_DIR), name="console-assets")


@app.get("/console/batches")
def console_batches() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.on_event("startup")
def bootstrap_defaults() -> None:
    ensure_builtin_agent_roles()
