"""
Data & ML reliability routes (Phase 7, brief P6).

  GET  /api/quality/summary              status counts over the stored per-lot reports
  GET  /api/quality/lots/{lot_id}        one lot's ingest quality report
  GET  /api/quality/drift                training vs recent distribution (PSI / KS)
  GET  /api/quality/monitoring           stored daily model-monitoring snapshots
  POST /api/quality/monitoring/snapshots recompute + store snapshots (admin)
"""
from fastapi import APIRouter, Depends, Query
from pymongo.database import Database

from app.api.deps import get_db
from app.quality import drift, monitoring
from app.services import yield_service
from app.utils.exceptions import ModelUnavailableError, NotFoundError
from app.utils.responses import success
from app.utils.security import require_roles

router = APIRouter()

_DRIFT_CACHE: dict = {}


@router.get("/quality/summary")
def quality_summary(db: Database = Depends(get_db)):
    pipeline = [{"$group": {"_id": "$status", "n": {"$sum": 1}}}]
    counts = {row["_id"]: row["n"] for row in db.data_quality_reports.aggregate(pipeline)}
    per_check: dict[str, int] = {}
    for doc in db.data_quality_reports.find({"status": {"$ne": "PASS"}}, {"_id": 0, "issues.check": 1}):
        for issue in doc.get("issues", []):
            per_check[issue["check"]] = per_check.get(issue["check"], 0) + 1
    return success(
        {
            "n_lots_reported": sum(counts.values()),
            "status_counts": counts,
            "issues_per_check": dict(sorted(per_check.items(), key=lambda kv: -kv[1])),
            "note": None if counts else "No quality reports stored yet — reports are written on ingest (seed_database.py / data import).",
        }
    )


@router.get("/quality/lots/{lot_id}")
def lot_quality(lot_id: str, db: Database = Depends(get_db)):
    report = db.data_quality_reports.find_one({"lot_id": lot_id}, {"_id": 0})
    if not report:
        raise NotFoundError(f"No data-quality report for {lot_id}. Reports are written on ingest (scripts/seed_database.py).")
    return success(report)


@router.get("/quality/drift")
def quality_drift(window_days: int = Query(default=30, ge=1, le=365), db: Database = Depends(get_db)):
    # Cache by data size: the report is a pure function of the stored lots/predictions.
    key = (window_days, db.lots.estimated_document_count(), db.predictions.estimated_document_count(), id(db))
    if key not in _DRIFT_CACHE:
        _DRIFT_CACHE.clear()
        _DRIFT_CACHE[key] = drift.drift_report_from_db(db, window_days=window_days)
    return success(_DRIFT_CACHE[key])


@router.get("/quality/monitoring")
def quality_monitoring(limit: int = Query(default=60, ge=1, le=400), db: Database = Depends(get_db)):
    snapshots = list(db.monitoring_snapshots.find({}, {"_id": 0}).sort("date", -1).limit(limit))
    snapshots.reverse()
    return success(
        {
            "summary": monitoring.summarize_snapshots(snapshots),
            "snapshots": snapshots,
            "note": None
            if snapshots
            else "No monitoring snapshots yet — they are stored by scripts/train_yield_model.py after backfill, or via POST /api/quality/monitoring/snapshots.",
            "label": "Back-filled daily snapshots over SYNTHETIC historical data.",
        }
    )


@router.post("/quality/monitoring/snapshots")
def rebuild_monitoring(db: Database = Depends(get_db), _actor: dict = Depends(require_roles("admin"))):
    try:
        bundle = yield_service.get_bundle(yield_service.MODEL_B_CLASSIFIER)
    except ModelUnavailableError:
        bundle = {}
    calibration = ((bundle.get("metadata") or {}).get("metrics") or {}).get("calibration") or {}
    threshold = float(calibration.get("decision_threshold") or monitoring.DEFAULT_DECISION_THRESHOLD)
    snapshots = monitoring.build_daily_snapshots(
        monitoring.monitoring_frame_from_db(db), threshold=threshold, model_version=bundle.get("version")
    )
    stored = monitoring.store_snapshots(db, snapshots)
    return success({"stored": stored, "summary": monitoring.summarize_snapshots(snapshots)})
