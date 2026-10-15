"""
Versioned response envelope (Phase 9). Every endpoint returns this shape;
the models exist so OpenAPI documents it (including every error response).

    success: {"success": true,  "data": ..., "error": null, "timestamp": ..., "api_version": "1"}
    error:   {"success": false, "data": null,
              "error": {"code": "NOT_FOUND", "message": ..., "request_id": ...}, ...}
"""
from typing import Any, Optional

from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: Optional[str] = None


class ErrorEnvelope(BaseModel):
    success: bool = False
    data: None = None
    error: ErrorBody
    timestamp: str
    api_version: str = "1"


class SuccessEnvelope(BaseModel):
    success: bool = True
    data: Any = None
    error: None = None
    timestamp: str
    api_version: str = "1"


# Attached to every router so the OpenAPI schema shows the consistent error format.
ERROR_RESPONSES = {
    status: {"model": ErrorEnvelope, "description": description}
    for status, description in {
        401: "Missing or invalid bearer token",
        403: "Role not permitted",
        404: "Resource not found",
        409: "Resource not in a state that allows this request",
        422: "Request failed validation",
        500: "Unexpected server error",
        503: "Dependency unavailable (database or model artifact)",
    }.items()
}
