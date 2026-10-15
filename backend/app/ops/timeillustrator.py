"""
Phase 7, P7-1 — timeillustrator: in-process hierarchical-span timer/counter library.

A small, dependency-free observability core, additive to (and not replacing)
`app.utils.metrics` (whose p50/p95 deque and Timer stay untouched and in use).

Design:
- Spans form a tree; ``to_dict`` flattens any node into a plain dict so tests
  and renderers do not need the Span class itself.
- All shared state lives inside ``MetricStore`` instances, NOT module-level
  globals. ``main.py``/ops routes own one store (P7-2/P7-3); tests own their
  own. This is the "no farming of shared global state" rule from the plan.
- Thread-safe: every mutation on a shared store is under a ``threading.Lock``.
- No I/O in this module; callers decide how to render or persist.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from contextlib import contextmanager
from types import TracebackType
from typing import Any, Deque, Dict, Iterator, List, Optional, Type
from operator import itemgetter
from typing import Any, Deque, Dict, Iterator, List, Optional

SpanDict = Dict[str, Any]


class Span:
    """One node in a timing tree, opened by ``start``/``child`` and closed by
    ``finish`` (or via the store's context manager).

    Timing semantics (DAEDALUS-style):
    - ``total_ms`` is the inclusive wall time from open to close.
    - ``self_ms`` is exclusive: own total minus the closed children's totals.
    - An unclosed span renders with ``is_active_span() == True`` so a crash mid-
      request is visible in the tree instead of silently dropped.
    """

    __slots__ = ("name", "start", "end", "children")

    def __init__(self, name: str):
        self.name = name
        self.start = time.perf_counter()
        self.end: Optional[float] = None
        self.children: List["Span"] = []

    def child(self, name: str) -> "Span":
        """Open a nested span under this one. Close it with ``finish()``."""
        s = Span(name)
        self.children.append(s)
        return s

    def finish(self) -> None:
        if self.end is None:
            self.end = time.perf_counter()

    def __enter__(self) -> "Span":
        return self

    def __exit__(self, exc_type: Optional[type], exc: Optional[BaseException], tb: Optional[TracebackType]) -> bool:
        self.finish()
        return False  # never swallow — failures propagate and stay visible

    def is_active_span(self) -> bool:
        return self.end is None

    @property
    def total_ms(self) -> float:
        if self.end is None:
            return 0.0
        return (self.end - self.start) * 1000

    @property
    def self_ms(self) -> float:
        if self.end is None:
            return 0.0
        own = self.total_ms
        closed_children = sum(c.total_ms for c in self.children if c.end is not None)
        return max(0.0, own - closed_children)

    def to_dict(self) -> SpanDict:
        return {
            "name": self.name,
            "total_ms": round(self.total_ms, 2),
            "self_ms": round(self.self_ms, 2),
            "active": self.is_active_span(),
            "children": [c.to_dict() for c in self.children],
        }


class MetricStore:
    """A bounded, thread-safe store of closed request-span trees plus counters."""

    def __init__(self, maxlen: int = 500):
        self._lock = threading.Lock()
        self._spans: Deque[Span] = deque(maxlen=maxlen)
        self._counters: Dict[str, float] = {}

    # -- counters ---------------------------------------------------------
    def incr(self, key: str, amount: float = 1.0) -> None:
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + amount

    def counter(self, key: str) -> float:
        """Process-lifetime cumulative value for one counter key."""
        with self._lock:
            return self._counters.get(key, 0.0)

    # -- spans ------------------------------------------------------------
    @contextmanager
    def span(self, name: str) -> Iterator[Span]:
        """Create-and-finish a top-level span, then file it into the ring."""
        s = Span(name)
        try:
            yield s
        finally:
            s.finish()
            self.record(s)

    def record(self, span: Span) -> None:
        """File a span as-is. An active span stays active: a request that
        crashed mid-flight must remain visible as such, not be silently closed
        by the store (the middleware closes spans itself before recording)."""
        with self._lock:
            self._spans.append(span)

    def recent(self, n: int = 50) -> List[Span]:
        with self._lock:
            return list(self._spans)[-n:]

    def __len__(self) -> int:
        with self._lock:
            return len(self._spans)

    # -- aggregation ------------------------------------------------------
    def aggregate(self, n: int = 500) -> List[SpanDict]:
        """Per top-level-span-name stats over the most recent ``n`` spans:
        count, avg/p50/p95/max ms, and how many were never closed (active)."""
        buckets: Dict[str, List[float]] = {}
        active: Dict[str, int] = {}
        for span in self.recent(n):
            end = span.end if span.end is not None else time.perf_counter()
            buckets.setdefault(span.name, []).append((end - span.start) * 1000)
            if span.is_active_span():
                active[span.name] = active.get(span.name, 0) + 1
        out: List[SpanDict] = []
        for name, samples in buckets.items():
            s = sorted(samples)
            p95_index = max(0, int(len(s) * 0.95) - 1)
            out.append(
                {
                    "name": name,
                    "count": len(s),
                    "avg_ms": round(sum(s) / len(s), 2),
                    "p50_ms": round(s[len(s) // 2], 2),
                    "p95_ms": round(s[p95_index], 2),
                    "max_ms": round(s[-1], 2),
                    "active_count": active.get(name, 0),
                }
            )
        out.sort(key=itemgetter("p95_ms"), reverse=True)
        return out

    # -- rendering --------------------------------------------------------
    def render_text(self, n: int = 50, max_depth: int = 4) -> str:
        """Human-readable trees of the ``n`` most recent spans, capped at
        ``max_depth`` (deeper subtrees collapse to a count)."""
        spans = self.recent(n)
        lines = [f"timeillustrator — {len(spans)} recent span(s):"]
        if not spans:
            lines.append("  (none — traffic will populate this as requests arrive)")
        for span in spans:
            lines.append(_render_span_tree(span, max_depth=max_depth))
        return "\n".join(lines)

    def render_json(self, n: int = 50, max_depth: int = 4) -> List[SpanDict]:
        return [_to_capped_dict(span, max_depth) for span in self.recent(n)]

    def counters_snapshot(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._counters)


def _render_span_tree(span: Span, max_depth: int) -> str:
    lines: List[str] = []

    def rec(s: Span, depth: int, prefix: str) -> None:
        marker = "▸" if s.is_active_span() else "·"
        line = f"{prefix}{marker} {s.name}  total={s.total_ms:.2f}ms"
        if s.children:
            line += f"  self={s.self_ms:.2f}ms"
        lines.append(line)
        if depth < max_depth:
            for c in s.children:
                rec(c, depth + 1, prefix + "  ")
        elif s.children:
            lines.append(f"{prefix}  … {len(s.children)} child(ren) truncated (max_depth={max_depth})")

    rec(span, 0, "")
    return "\n".join(lines)


def _to_capped_dict(span: Span, max_depth: int) -> SpanDict:
    def rec(s: Span, depth: int) -> SpanDict:
        d = s.to_dict()
        if not s.children:
            d["children"] = []
        elif depth >= max_depth:
            d["children"] = [{"__truncated__": len(s.children)}]
        else:
            d["children"] = [rec(c, depth + 1) for c in s.children]
        return d

    return rec(span, 0)


__all__ = ["Span", "MetricStore", "SpanDict"]
