"""Per-request id (Phase 9): set by middleware, echoed in X-Request-ID, error bodies and logs."""
import logging
import uuid
from contextvars import ContextVar

_REQUEST_ID: ContextVar[str | None] = ContextVar("request_id", default=None)


def new_request_id(incoming: str | None = None) -> str:
    """Use a sane client-supplied id, else mint one. Returns the id now in context."""
    rid = incoming if incoming and len(incoming) <= 64 and incoming.replace("-", "").isalnum() else uuid.uuid4().hex
    _REQUEST_ID.set(rid)
    return rid


def current_request_id() -> str | None:
    return _REQUEST_ID.get()


class RequestIdLogFilter(logging.Filter):
    """Adds `request_id` to every log record ('-' outside a request)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _REQUEST_ID.get() or "-"
        return True
