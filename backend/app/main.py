"""
WafGuard AI — FastAPI application entrypoint.

Run with:
    uvicorn app.main:app --reload
from inside backend/, with the virtualenv activated.
"""
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    auth,
    copilot,
    dashboard,
    data_import,
    defects,
    equipment,
    lots,
    models_info,
    recommendations,
    risk,
    rootcause,
    similarity,
    simulation,
    wafers,
    yield_prediction,
)
from app.config import get_settings
from app.database import mongodb
from app.utils.exceptions import register_exception_handlers
from app.utils.logging import get_logger
from app.utils.metrics import get_request_metrics, record_request_duration
from app.utils.responses import success

logger = get_logger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting WafGuard AI backend...")
    mongodb.connect()
    yield
    mongodb.disconnect()


app = FastAPI(
    title="WafGuard AI",
    description="Predict. Diagnose. Prevent. Improve Yield. — AI decision-support platform for semiconductor process engineers.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)


@app.middleware("http")
async def request_timing_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    record_request_duration(duration_ms)
    response.headers["X-Process-Time-Ms"] = f"{duration_ms:.2f}"
    logger.info("%s %s -> %s (%.2fms)", request.method, request.url.path, response.status_code, duration_ms)
    return response


@app.get("/api/health")
def health_check():
    db_connected = mongodb.is_connected()
    return success(
        {
            "status": "healthy" if db_connected else "degraded",
            "database": "connected" if db_connected else "disconnected",
            "database_note": None
            if db_connected
            else "MongoDB is not reachable. Install/start it and run scripts/seed_database.py (see README).",
            "request_metrics": get_request_metrics(),
        }
    )


app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(dashboard.router, prefix="/api", tags=["dashboard"])
app.include_router(lots.router, prefix="/api", tags=["lots"])
app.include_router(yield_prediction.router, prefix="/api", tags=["yield"])
app.include_router(rootcause.router, prefix="/api", tags=["root-cause"])
app.include_router(defects.router, prefix="/api", tags=["defects"])
app.include_router(wafers.router, prefix="/api", tags=["wafers"])
app.include_router(equipment.router, prefix="/api", tags=["equipment"])
app.include_router(risk.router, prefix="/api", tags=["risk"])
app.include_router(recommendations.router, prefix="/api", tags=["recommendations"])
app.include_router(similarity.router, prefix="/api", tags=["similarity"])
app.include_router(simulation.router, prefix="/api", tags=["simulation"])
app.include_router(models_info.router, prefix="/api", tags=["models"])
app.include_router(data_import.router, prefix="/api", tags=["data"])
app.include_router(copilot.router, prefix="/api", tags=["copilot"])
