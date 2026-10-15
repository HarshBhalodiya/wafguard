"""
Background job routes (Phase 9):

  POST /api/jobs               create a job -> 202 {job_id, status: queued}
  GET  /api/jobs               recent jobs (no results)
  GET  /api/jobs/{id}          status
  GET  /api/jobs/{id}/result   result (409 while queued/running or when failed)
"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pymongo.database import Database

from app.api.deps import get_db
from app.jobs import runner
from app.jobs.tasks import TASKS
from app.utils.exceptions import NotFoundError, WafGuardError
from app.utils.responses import success
from app.utils.security import require_roles

router = APIRouter()
_JOB_ROLES = ("process_engineer", "yield_engineer", "equipment_engineer", "admin")


class JobRequest(BaseModel):
    type: str = Field(description=f"One of: {', '.join(sorted(TASKS))}")
    params: dict = Field(default_factory=dict)


class JobNotReadyError(WafGuardError):
    code = "JOB_NOT_READY"
    status_code = 409


class JobFailedError(WafGuardError):
    code = "JOB_FAILED"
    status_code = 409


@router.post("/jobs", status_code=202)
def create_job(payload: JobRequest, db: Database = Depends(get_db), actor: dict = Depends(require_roles(*_JOB_ROLES))):
    job = runner.submit(db, payload.type, payload.params, submitted_by=actor.get("username"))
    return JSONResponse(status_code=202, content=success({k: job[k] for k in ("job_id", "type", "status", "created_at")}))


@router.get("/jobs")
def list_jobs(limit: int = Query(default=20, ge=1, le=200), db: Database = Depends(get_db)):
    jobs = list(db.jobs.find({}, {"_id": 0, "result": 0}).sort("created_at", -1).limit(limit))
    return success({"jobs": jobs, "job_types": sorted(TASKS)})


@router.get("/jobs/{job_id}")
def job_status(job_id: str, db: Database = Depends(get_db)):
    job = runner.get(db, job_id)
    if not job:
        raise NotFoundError(f"Job {job_id} was not found.")
    return success(job)


@router.get("/jobs/{job_id}/result")
def job_result(job_id: str, db: Database = Depends(get_db)):
    job = runner.get(db, job_id, include_result=True)
    if not job:
        raise NotFoundError(f"Job {job_id} was not found.")
    if job["status"] == "failed":
        raise JobFailedError(f"Job {job_id} failed: {job.get('error')}")
    if job["status"] != "succeeded":
        raise JobNotReadyError(f"Job {job_id} is {job['status']}; poll GET /api/jobs/{job_id} until it succeeds.")
    return success({"job_id": job_id, "type": job["type"], "result": job["result"]})
