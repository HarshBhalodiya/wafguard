"""
Custom exceptions + FastAPI exception handlers.

Goal (spec section 41): the system must gracefully handle missing datasets,
missing models, a down database, bad input, etc. — and never leak a Python
stack trace to the frontend. Technical detail is logged server-side only.
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.utils.logging import get_logger
from app.utils.responses import error

logger = get_logger(__name__)


class WafGuardError(Exception):
    """Base class for domain errors that map to a known error code."""

    code = "INTERNAL_ERROR"
    status_code = 500

    def __init__(self, message: str, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class NotFoundError(WafGuardError):
    code = "NOT_FOUND"
    status_code = 404


class ValidationFailedError(WafGuardError):
    code = "VALIDATION_ERROR"
    status_code = 422


class DatasetUnavailableError(WafGuardError):
    code = "DATASET_UNAVAILABLE"
    status_code = 503


class ModelUnavailableError(WafGuardError):
    code = "MODEL_UNAVAILABLE"
    status_code = 503


class DatabaseUnavailableError(WafGuardError):
    code = "DATABASE_UNAVAILABLE"
    status_code = 503


class UnsupportedFileError(WafGuardError):
    code = "UNSUPPORTED_FILE"
    status_code = 415


class InferenceError(WafGuardError):
    code = "INFERENCE_ERROR"
    status_code = 500


class UnauthorizedError(WafGuardError):
    code = "UNAUTHORIZED"
    status_code = 401


class ForbiddenError(WafGuardError):
    code = "FORBIDDEN"
    status_code = 403


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(WafGuardError)
    async def wafguard_error_handler(request: Request, exc: WafGuardError):
        logger.warning("%s %s -> %s: %s", request.method, request.url.path, exc.code, exc.message)
        return JSONResponse(status_code=exc.status_code, content=error(exc.code, exc.message))

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=error("HTTP_ERROR", str(exc.detail)),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=error("VALIDATION_ERROR", "Request payload failed validation."),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=error("INTERNAL_ERROR", "An unexpected error occurred. Check server logs."),
        )
