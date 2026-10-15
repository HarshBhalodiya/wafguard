"""
Job task registry. Each task: `fn(db, params) -> JSON-serialisable result`.

  data_quality_batch   validate a posted sensor batch (or stored lots) and store
                       per-lot quality reports                 (sensor batches)
  wafer_map_batch      classify every wafer map of a lot with the CNN
                                                                (image processing)
  root_cause_batch     rank root-cause candidates for several lots
  upcoming_lot_risk    score the derived upcoming-lot queue

Parameters are validated here (ValidationFailedError -> the job is rejected at
submission with a 422, before anything is queued).
"""
from __future__ import annotations

import pandas as pd

from app.utils.exceptions import ValidationFailedError

MAX_LOTS_PER_JOB = 200
MAX_TELEMETRY_ROWS = 200_000


def _lot_ids(params: dict) -> list[str]:
    lot_ids = params.get("lot_ids")
    if not isinstance(lot_ids, list) or not lot_ids or not all(isinstance(x, str) for x in lot_ids):
        raise ValidationFailedError("params.lot_ids must be a non-empty list of lot ids")
    if len(lot_ids) > MAX_LOTS_PER_JOB:
        raise ValidationFailedError(f"at most {MAX_LOTS_PER_JOB} lots per job")
    return lot_ids


def data_quality_batch(db, params: dict) -> dict:
    from app.quality.validation import store_reports, summarize, validate_tables

    telemetry = params.get("telemetry")
    if telemetry is not None:
        tel = pd.DataFrame(telemetry)
        lots = pd.DataFrame(params.get("lots") or [{"lot_id": x} for x in sorted(tel["lot_id"].astype(str).unique())])
    else:
        lot_ids = _lot_ids(params)
        lots = pd.DataFrame(list(db.lots.find({"lot_id": {"$in": lot_ids}}, {"_id": 0, "lot_id": 1, "process_start": 1, "process_end": 1, "yield": 1})))
        tel = None
        if lots.empty:
            raise ValidationFailedError("none of the requested lots exist")
    reports = validate_tables(lots, tel, expected_channels=params.get("expected_channels"))
    store_reports(db, reports)
    return {"summary": summarize(reports), "reports": reports[["lot_id", "status", "n_fail", "n_warn"]].to_dict("records")}


def wafer_map_batch(db, params: dict) -> dict:
    from app.services import defect_service

    lot_id = params.get("lot_id")
    if not isinstance(lot_id, str) or not lot_id:
        raise ValidationFailedError("params.lot_id is required")
    wafer_ids = [w["wafer_id"] for w in db.wafers.find({"lot_id": lot_id}, {"_id": 0, "wafer_id": 1})]
    results = []
    for wafer_id in wafer_ids:
        classified = defect_service.classify_wafer_by_id(db, wafer_id)
        results.append(
            {
                "wafer_id": wafer_id,
                "pattern": classified.get("wafer_map_pattern"),
                "confidence": classified.get("wafer_map_confidence"),
            }
        )
    counts: dict[str, int] = {}
    for r in results:
        counts[str(r["pattern"])] = counts.get(str(r["pattern"]), 0) + 1
    return {"lot_id": lot_id, "n_wafers": len(results), "pattern_counts": counts, "wafers": results}


def root_cause_batch(db, params: dict) -> dict:
    from app.services import rootcause_service

    out = []
    for lot_id in _lot_ids(params):
        try:
            result = rootcause_service.compute_root_cause(db, lot_id, top_n=3)
            out.append({"lot_id": lot_id, "candidates": [{"candidate": c["candidate"], "score": c["score"], "confidence": c["confidence"]} for c in result["candidates"]]})
        except Exception as exc:  # noqa: BLE001 - one bad lot is reported, not fatal to the batch
            out.append({"lot_id": lot_id, "error": str(exc)})
    return {"n_lots": len(out), "results": out}


def upcoming_lot_risk(db, params: dict) -> dict:
    from app.risk import upcoming_lot_risk as engine

    limit = int(params.get("limit", 20))
    if not 1 <= limit <= 200:
        raise ValidationFailedError("params.limit must be between 1 and 200")
    return engine.upcoming_lot_risks(db, limit=limit)


TASKS = {
    "data_quality_batch": data_quality_batch,
    "wafer_map_batch": wafer_map_batch,
    "root_cause_batch": root_cause_batch,
    "upcoming_lot_risk": upcoming_lot_risk,
}


def validate_submission(job_type: str, params: dict) -> None:
    """Cheap structural checks at submission time (full validation happens in the task)."""
    if job_type not in TASKS:
        raise ValidationFailedError(f"unknown job type {job_type!r}; available: {sorted(TASKS)}")
    if job_type == "root_cause_batch":
        _lot_ids(params)
    if job_type == "data_quality_batch":
        telemetry = params.get("telemetry")
        if telemetry is None:
            _lot_ids(params)
        elif not isinstance(telemetry, list) or len(telemetry) > MAX_TELEMETRY_ROWS:
            raise ValidationFailedError(f"params.telemetry must be a list of at most {MAX_TELEMETRY_ROWS} rows")
    if job_type == "wafer_map_batch" and not params.get("lot_id"):
        raise ValidationFailedError("params.lot_id is required")
