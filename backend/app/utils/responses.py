"""
Consistent API response envelope (spec: section 29 — API Response Contract).

Every endpoint returns:
    {"success": bool, "data": ..., "error": null | {...}, "timestamp": "..."}
"""
from datetime import datetime, timezone
from typing import Any, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def success(data: Any = None) -> dict:
    return {
        "success": True,
        "data": data,
        "error": None,
        "timestamp": _now(),
    }


def error(code: str, message: str) -> dict:
    return {
        "success": False,
        "data": None,
        "error": {"code": code, "message": message},
        "timestamp": _now(),
    }
