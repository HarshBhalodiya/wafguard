"""
Job store + runner (Phase 9).

Status lives in Mongo `jobs` (queued -> running -> succeeded | failed), so any
API process can answer "where is my job?". Work runs on a small in-process
thread pool — the simplest queue that needs no extra service. The trade-off is
recorded in docs/DECISIONS.md (D-052): jobs do not survive an API restart; a
job left `running`/`queued` by a dead process is marked `failed` on startup
(`recover_orphans`) instead of hanging forever. Swap the executor for RQ or
Celery when jobs must outlive the API process — the store/status contract
stays the same.
"""
from __future__ import annotations

import json
import traceback
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone

from app.jobs.tasks import TASKS, validate_submission
from app.utils.logging import get_logger

logger = get_logger(__name__)
MAX_WORKERS = 2
_EXECUTOR = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="wafguard-job")
_FUTURES: dict[str, Future] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value):
    return json.loads(json.dumps(value, default=str))


def submit(db, job_type: str, params: dict, submitted_by: str | None) -> dict:
    validate_submission(job_type, params)
    job_id = uuid.uuid4().hex
    stored_params = dict(params)
    if "telemetry" in stored_params:  # a sensor batch can be large — store its size, not its rows
        stored_params["telemetry"] = f"<{len(stored_params['telemetry'] or [])} rows>"
    doc = {
        "job_id": job_id,
        "type": job_type,
        "params": _json_safe(stored_params),
        "status": "queued",
        "submitted_by": submitted_by,
        "created_at": _now(),
        "started_at": None,
        "finished_at": None,
        "error": None,
        "result": None,
    }
    db.jobs.insert_one(dict(doc))
    _FUTURES[job_id] = _EXECUTOR.submit(_run, db, job_id, job_type, params)
    return doc


def _run(db, job_id: str, job_type: str, params: dict) -> None:
    db.jobs.update_one({"job_id": job_id}, {"$set": {"status": "running", "started_at": _now()}})
    try:
        result = _json_safe(TASKS[job_type](db, params))
        db.jobs.update_one({"job_id": job_id}, {"$set": {"status": "succeeded", "finished_at": _now(), "result": result}})
    except Exception as exc:  # noqa: BLE001 - every failure is recorded on the job
        logger.error("job %s (%s) failed: %s\n%s", job_id, job_type, exc, traceback.format_exc())
        db.jobs.update_one({"job_id": job_id}, {"$set": {"status": "failed", "finished_at": _now(), "error": str(exc)}})
    finally:
        _FUTURES.pop(job_id, None)


def get(db, job_id: str, include_result: bool = False) -> dict | None:
    projection = {"_id": 0} if include_result else {"_id": 0, "result": 0}
    return db.jobs.find_one({"job_id": job_id}, projection)


def wait(job_id: str, timeout: float = 60.0) -> None:
    """Block until a job submitted by THIS process finishes (tests / CLI use)."""
    future = _FUTURES.get(job_id)
    if future is not None:
        future.result(timeout=timeout)


def recover_orphans(db) -> int:
    """Mark jobs left queued/running by a previous process as failed."""
    result = db.jobs.update_many(
        {"status": {"$in": ["queued", "running"]}, "job_id": {"$nin": list(_FUTURES)}},
        {"$set": {"status": "failed", "finished_at": _now(), "error": "API process restarted before the job finished"}},
    )
    return result.modified_count
