"""Local dashboard application.

Agent conversations and scheduling belong to Codex Desktop.  This process only
serves the read-oriented dashboard and the deterministic paper-engine control
surface.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from openalphastack.app import dashboard


@asynccontextmanager
async def lifespan(_: FastAPI):
    dashboard.reset_sse_shutdown()
    yield
    dashboard.arm_forced_exit_timer()
    dashboard.shutdown_sse()


app = FastAPI(title="OpenAlphaStack", lifespan=lifespan)
app.mount(
    "/dashboard/assets",
    StaticFiles(directory=dashboard.DASHBOARD_ASSETS_DIR, check_dir=False),
    name="dashboard-assets",
)
app.include_router(dashboard.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "openalphastack-dashboard",
        "time": datetime.now().isoformat(),
    }
