"""
Lightweight in-process request/inference metrics (spec section 43).

Not a replacement for real observability tooling — just enough to expose
p95-style timing on /api/health and in logs without adding Dynatrace or
any other enterprise APM to the local MVP.
"""
import threading
import time
from collections import deque
from statistics import mean

_LOCK = threading.Lock()
_REQUEST_DURATIONS_MS: deque = deque(maxlen=500)


def record_request_duration(duration_ms: float) -> None:
    with _LOCK:
        _REQUEST_DURATIONS_MS.append(duration_ms)


def get_request_metrics() -> dict:
    with _LOCK:
        samples = list(_REQUEST_DURATIONS_MS)
    if not samples:
        return {"count": 0, "avg_ms": None, "p95_ms": None}
    sorted_samples = sorted(samples)
    p95_index = max(0, int(len(sorted_samples) * 0.95) - 1)
    return {
        "count": len(samples),
        "avg_ms": round(mean(samples), 2),
        "p95_ms": round(sorted_samples[p95_index], 2),
    }


class Timer:
    """Simple stopwatch: `with Timer() as t: ...` then `t.elapsed_ms`."""

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.elapsed_ms = (time.perf_counter() - self._start) * 1000
