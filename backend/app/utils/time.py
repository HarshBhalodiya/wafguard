"""Shared timezone-aware "now" helper (datetime.utcnow() is deprecated)."""
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
