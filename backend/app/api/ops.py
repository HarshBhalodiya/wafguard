"""
Phase 7, P7-3 — live operational-visibility endpoints.

Backed entirely by the timeillustrator ``MetricStore`` that main.py's
request-timing middleware fills. Shallow-data discipline (P6/D-035): no DB
contents, no filesystem paths, no secrets — just latency/counter numbers
about the API process itself.

Two routes, both admin-only (the RBAC matrix tier: operational metrics are
an ops concern, and the numbers/exposure follow the D-027 "auth on every
route" rule — they simply raise the role bar rather than opening to viewers):

- ``GET /api/ops/metrics``      — per-endpoint latency aggregates + counters.
- ``GET /api/ops/health-probe`` — robustness WITHOUT mutating anything.
"""
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from app.ops import registry
from app.ops.registry import METRIC_STORE  # re-exported for convenience; live reads go through registry
from app.utils.responses import success
from app.utils.security import require_roles

router = APIRouter()


@router.get("/ops/metrics")
def ops_metrics(
    window: int = 100,
    _admin: Dict[str, Any] = Depends(require_roles("admin")),
) -> Dict[str, Any]:
    """Live per-endpoint latency picture from the in-process ring buffer."""
    window = max(1, min(window, 500))  # bound the view to the store's own capacity
    store = registry.METRIC_STORE  # live read: tests may swap the store between requests
    recent = store.recent(window)
    return success(
        {
            "window_spans": len(recent),
            "endpoints": store.aggregate(n=window),
            "counters": store.counters_snapshot(),
        }
    )


@router.get("/ops/health-probe")
def ops_health_probe(
    _admin: Dict[str, Any] = Depends(require_roles("admin")),
) -> Dict[str, Any]:
    """Robustness view of the store itself — no side effects on the app."""
    store = registry.METRIC_STORE
    spans = store.recent(500)
    active = [s for s in spans if s.is_active_span()]
    aggregate = store.aggregate(n=500)
    return success(
        {
            "store": {
                "capacity": 500,
                "recorded": len(spans),
                "active_from_crashed_requests": len(active),
            },
            "slowest_endpoints": aggregate[:5],
            "note": "In-process only: numbers reset when the API process restarts. Not a substitute for external uptime monitoring.",
        }
    )
