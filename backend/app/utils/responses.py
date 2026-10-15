"""
Consistent API response envelope (spec: section 29 — API Response Contract).

Every endpoint returns:
    {"success": bool, "data": ..., "error": null | {...}, "timestamp": "..."}
"""
from datetime import datetime, timezone
from typing import Any, Optional

from app.utils.request_context import current_request_id

# Phase 9: the envelope carries the API contract version. Bump only on a
# breaking change to request/response shapes (additive fields do not bump it).
API_VERSION = "1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def success(data: Any = None) -> dict:
    return {
        "success": True,
        "data": data,
        "error": None,
        "timestamp": _now(),
        "api_version": API_VERSION,
    }


def error(code: str, message: str) -> dict:
    return {
        "success": False,
        "data": None,
        "error": {"code": code, "message": message, "request_id": current_request_id()},
        "timestamp": _now(),
        "api_version": API_VERSION,
    }
