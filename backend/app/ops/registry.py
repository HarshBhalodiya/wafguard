"""Phase 7, P7-3 — process-owned MetricStore singleton.

Centralizing the single MOS instance here instead of in ``app.main`` avoids a
circular import (ops routes import the store; main imports the ops routes).
The store itself still carries NO global mutable state *other than being a
process-lifetime singleton*, which main.py and the ops routes both need to
agree on. Tests that need isolation can construct their own ``MetricStore``
directly from ``app.ops.timeillustrator`` — the plan's "no farming of shared
global state" rule applies to state farming by hidden getter-clearers, not to
a documented, purpose-built process singleton.
"""
from app.ops.timeillustrator import MetricStore

METRIC_STORE = MetricStore(maxlen=500)


def swap_store(store: MetricStore) -> MetricStore:
    """Replace the process store (tests use this; never a fresh import of a
    stale name). Returns the previous store so callers can restore it."""
    global METRIC_STORE
    previous = METRIC_STORE
    METRIC_STORE = store
    return previous
