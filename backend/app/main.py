"""
WafGuard AI — FastAPI application entrypoint.

Run with:
    uvicorn app.main:app --reload
from inside backend/, with the virtualenv activated.

Routing (Phase 9): every router is mounted twice — under the versioned prefix
`/api/v1` (shown in the OpenAPI docs) and under `/api` (a stable alias of v1,
kept so existing clients and the current UI keep working). The response
envelope carries `api_version`; a breaking contract change would add `/api/v2`
next to v1 instead of mutating it.
"""
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.responses import ORJSONResponse

from app.api import (
    alerts,
    auth,
    canonical,
    copilot,
    dashboard,
    data_import,
    defects,
    equipment,
    jobs,
    lot_risk,
    ops,
    lots,
    models_info,
    quality,
    recommendations,
    risk,
    rootcause,
    similarity,
    simulation,
    timeline,
    wafers,
    whatif,
    yield_prediction,
)
from app.config import get_settings
from app.database import mongodb
from app.ops.readiness import shallow_readiness
from app.ops import registry as _ops_registry
from app.ops.timeillustrator import Span
from app.schemas.envelope import ERROR_RESPONSES
from app.utils.exceptions import register_exception_handlers
from app.utils.logging import get_logger
from app.utils.metrics import get_request_metrics, record_request_duration
from app.utils.request_context import new_request_id
from app.utils.responses import API_VERSION, error, success
from app.utils.security import get_current_user

logger = get_logger(__name__)
settings = get_settings()

# P7-2/P7-3: the process-owned MetricStore lives in app.ops.registry (not here)
# to avoid a circular import — ops routes import the store, main imports the
# routes. See the docstring in app/ops/registry.py.


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting WafGuard AI backend (API v%s)...", API_VERSION)
    mongodb.connect()
    # Phase 9: load every model artifact once, at startup — not per request.
    from app.ops.warmup import warm_models

    app.state.warmup = warm_models()
    if mongodb.is_connected():
        from app.jobs.runner import recover_orphans

        try:
            orphaned = recover_orphans(mongodb.get_database())
            if orphaned:
                logger.warning("Marked %d orphaned background job(s) as failed.", orphaned)
        except Exception:  # noqa: BLE001 - job bookkeeping must not block startup
            logger.exception("Could not recover orphaned jobs.")
    yield
    mongodb.disconnect()


app = FastAPI(
    title="WafGuard AI",
    description=(
        "Predict. Diagnose. Prevent. Improve Yield. — AI decision-support platform for semiconductor process "
        "engineers. All data is SYNTHETIC. Versioned API: /api/v1 (alias: /api)."
    ),
    version="0.2.0",
    lifespan=lifespan,
    # Safety net at the serialization layer: orjson renders NaN/Infinity as null
    # (stdlib json raises ValueError and turns the request into a 500). Writes are
    # scrubbed at the repository/seeder level too — see app/utils/mongo_sanitize.py —
    # so this only has to catch anything that slips past the write path.
    default_response_class=ORJSONResponse,
)
app.state.warmup = {"models": [], "n_loaded": 0, "n_total": 0}

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID"],
    expose_headers=["X-Request-ID", "X-API-Version", "X-Process-Time-Ms"],
)

register_exception_handlers(app)


@app.middleware("http")
async def request_timing_middleware(request: Request, call_next):
    """P7-2: same wall-time bookkeeping as before, plus a per-request span
    filed into METRIC_STORE so /api/ops/* can query per-endpoint p50/p95.
    Phase 9: every request gets an id (X-Request-ID, echoed in error bodies and
    log lines) and every response says which API contract version produced it."""
    request_id = new_request_id(request.headers.get("x-request-id"))
    span_name = f"{request.method} {request.url.path}"
    start = time.perf_counter()
    root_span = Span(span_name)
    status_code = 500  # an exception before a response exists counts as a server error
    try:
        response = await call_next(request)
        status_code = response.status_code
    finally:
        duration_ms = (time.perf_counter() - start) * 1000
        root_span.finish()
        store = _ops_registry.METRIC_STORE  # live read: tests may swap the store
        store.record(root_span)
        store.incr(f"requests:{span_name}")
        if status_code >= 400:
            store.incr(f"requests:{span_name}:errors")
    response.headers["X-Process-Time-Ms"] = f"{duration_ms:.2f}"
    response.headers["X-Request-ID"] = request_id
    response.headers["X-API-Version"] = API_VERSION
    logger.info("%s %s -> %s (%.2fms)", request.method, request.url.path, response.status_code, duration_ms)
    return response


def _health_payload() -> tuple[bool, dict]:
    db_connected = mongodb.is_connected()
    readiness = shallow_readiness(settings)
    # "healthy" now means *actually serviceable*: DB up AND the dataset +
    # trained tabular bundles present (Phase 6, P6-3). A DB-only green light
    # was a lie of omission — every prediction endpoint 503s without bundles.
    serviceable = db_connected and readiness["tabular_models_ready"]
    if serviceable:
        status, note = "healthy", None
    elif not db_connected:
        status = "degraded"
        note = "MongoDB is not reachable. Install/start it and run scripts/seed_database.py (see README)."
    else:
        status = "degraded"
        note = (
            "MongoDB is up but the dataset and/or trained tabular models are missing — "
            "run 'python scripts/build_dataset.py --all' (or scripts/doctor.py for details)."
        )
    warmup = getattr(app.state, "warmup", {}) or {}
    return serviceable, {
        "status": status,
        "api_version": API_VERSION,
        "database": "connected" if db_connected else "disconnected",
        "database_note": note if not db_connected else None,
        "readiness": readiness,
        "readiness_note": note if db_connected else None,
        # Phase 9: which artifacts were loaded at startup (names + booleans only).
        "models_loaded": {m["name"]: m["loaded"] for m in warmup.get("models", [])},
        "request_metrics": get_request_metrics(),
    }


@app.get("/", include_in_schema=False)
def root_redirect():
    """Bare root has no resource — send a browser to the interactive docs
    instead of answering the 404 error envelope that used to appear here.
    307 (not 308) so a stale cached redirect can never pin an old URL."""
    return RedirectResponse(url="/docs", status_code=307)


@app.get("/api/health")
def health_check():
    return success(_health_payload()[1])


@app.get("/api/health/live")
def liveness():
    """The process is up (container liveness probe). Never touches dependencies."""
    return success({"status": "alive", "api_version": API_VERSION})


@app.get("/api/health/ready")
def readiness_probe():
    """503 until the API can actually serve predictions (container readiness probe)."""
    serviceable, payload = _health_payload()
    if serviceable:
        return success(payload)
    return JSONResponse(status_code=503, content=error("NOT_READY", payload.get("readiness_note") or payload.get("database_note") or "not ready"))


# Phase 4 (P4-1): every router except auth (login) requires a valid JWT. The
# viewer role becomes a real read-only role; per-action role gates live on the
# individual mutation routes (data import, what-if, copilot, defect analyze,
# alert-channel admin).
_REQUIRE_AUTH = [Depends(get_current_user)]

# (router, tag, needs auth). Order matters only where paths overlap.
ROUTERS = [
    (auth.router, "auth", False),
    (dashboard.router, "dashboard", True),
    (lots.router, "lots", True),
    (yield_prediction.router, "yield", True),
    (rootcause.router, "root-cause", True),
    (defects.router, "defects", True),
    (wafers.router, "wafers", True),
    (equipment.router, "equipment", True),
    (risk.router, "risk", True),
    # PHASE 4 (P3): pre-run risk for lots that have not run yet.
    (lot_risk.router, "lot-risk", True),
    (recommendations.router, "recommendations", True),
    (similarity.router, "similarity", True),
    (simulation.router, "simulation", True),
    # Phase 6 (brief P5): counterfactual engine with uncertainty ranges + blocked impossible changes.
    (whatif.router, "whatif", True),
    # Phase 7 (brief P6): ingest data quality, drift, model monitoring.
    (quality.router, "quality", True),
    (timeline.router, "timeline", True),
    (models_info.router, "models", True),
    (data_import.router, "data", True),
    (copilot.router, "copilot", True),
    (alerts.router, "alerts", True),
    # Phase 7 (P7-3): live ops observability. Admin-only via require_roles on the
    # routes themselves, since operational latency numbers are an ops concern.
    (ops.router, "ops", True),
    # Phase 9: canonical brief route names + background jobs.
    (canonical.router, "canonical", True),
    (jobs.router, "jobs", True),
]

for _router, _tag, _auth in ROUTERS:
    _deps = _REQUIRE_AUTH if _auth else []
    app.include_router(_router, prefix="/api/v1", tags=[_tag], dependencies=_deps, responses=ERROR_RESPONSES)
    app.include_router(_router, prefix="/api", tags=[_tag], dependencies=_deps, include_in_schema=False)
