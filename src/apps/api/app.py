from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.apps.api.bootstrap import ensure_builtin_agent_roles
from src.apps.api.routers import (
    agents_router,
    artifacts_router,
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
app.include_router(artifacts_router)
app.include_router(runs_router)
app.include_router(reviews_router)

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
VUE_DIST_DIR = WEB_DIR / "dist"
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
app.mount("/console/assets", StaticFiles(directory=WEB_DIR), name="console-assets")
if VUE_DIST_DIR.exists():
    app.mount("/console/vue", StaticFiles(directory=VUE_DIST_DIR), name="console-vue")


def _agent_registry_page() -> FileResponse:
    vue_entry = VUE_DIST_DIR / "index.html"
    if vue_entry.exists():
        return FileResponse(vue_entry)
    return FileResponse(WEB_DIR / "agents.html")


@app.get("/")
def console_home() -> FileResponse:
    return _agent_registry_page()


@app.get("/console/batches")
def console_batches() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/console/agents")
def console_agents() -> FileResponse:
    return _agent_registry_page()


@app.get("/console/batches/{batch_id}")
def console_batch_detail(batch_id: str) -> FileResponse:
    return FileResponse(WEB_DIR / "batch-detail.html")


@app.get("/console/runs/{run_id}")
def console_run_detail(run_id: str) -> FileResponse:
    return FileResponse(WEB_DIR / "run-detail.html")


@app.on_event("startup")
def bootstrap_defaults() -> None:
    ensure_builtin_agent_roles()
