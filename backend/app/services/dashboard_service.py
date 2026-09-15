"""
Aggregates the executive dashboard view (spec section 21): "what is
happening in the fab right now?" Root-cause candidates for the top
at-risk lots are read from cache when available and computed (then
cached) otherwise, so repeated dashboard loads stay fast.
"""
from collections import defaultdict
from datetime import datetime

from app.database.repositories.alert_repository import AlertRepository
from app.database.repositories.equipment_repository import EquipmentRepository
from app.database.repositories.lot_repository import LotRepository
from app.database.repositories.rootcause_repository import RootCauseRepository
from app.database.repositories.wafer_repository import WaferRepository
from app.services import rootcause_service

TOP_ROOT_CAUSE_LOT_CAP = 50  # bound how many lots we'll compute-on-demand root-cause for per dashboard load


def _parse_ts(value) -> datetime:
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def _trend_by_week(lots: list[dict], value_key: str) -> list[dict]:
    buckets = defaultdict(list)
    for lot in lots:
        value = lot.get(value_key)
        if value is None or lot.get("process_start") is None:
            continue
        ts = _parse_ts(lot["process_start"])
        week_key = ts.strftime("%Y-W%W")
        buckets[week_key].append(value)
    return [
        {"period": period, "value": round(sum(values) / len(values), 2), "n_lots": len(values)}
        for period, values in sorted(buckets.items())
    ]


def get_dashboard_summary(db) -> dict:
    lot_repo = LotRepository(db)
    equipment_repo = EquipmentRepository(db)
    wafer_repo = WaferRepository(db)
    alert_repo = AlertRepository(db)
    rootcause_repo = RootCauseRepository(db)

    all_lots = lot_repo.find_many({})
    completed = [l for l in all_lots if l.get("yield") is not None]
    predicted = [l for l in all_lots if l.get("predicted_yield") is not None]
    at_risk = [l for l in all_lots if l.get("risk_level") in ("MEDIUM", "HIGH")]
    high_risk = [l for l in all_lots if l.get("risk_level") == "HIGH"]

    total_wafers = wafer_repo.count({})
    failed_wafers = wafer_repo.count({"yield_status": "fail"})

    candidate_scores: dict[str, float] = {}
    ranked_at_risk = sorted(at_risk, key=lambda l: l.get("risk_probability", 0), reverse=True)[:TOP_ROOT_CAUSE_LOT_CAP]
    for lot in ranked_at_risk:
        cached = rootcause_repo.latest_for_lot(lot["lot_id"])
        result = cached or rootcause_service.compute_root_cause(db, lot["lot_id"])
        if result["candidates"]:
            top = result["candidates"][0]
            candidate_scores[top["candidate"]] = candidate_scores.get(top["candidate"], 0.0) + top["score"]

    total_score = sum(candidate_scores.values()) or 1.0
    top_root_causes = sorted(
        ({"candidate": k, "share": round(v / total_score, 4)} for k, v in candidate_scores.items()),
        key=lambda d: d["share"],
        reverse=True,
    )[:5]

    return {
        "overall_yield": round(sum(l["yield"] for l in completed) / len(completed), 2) if completed else None,
        "avg_predicted_yield": round(sum(l["predicted_yield"] for l in predicted) / len(predicted), 2) if predicted else None,
        "at_risk_lot_count": len(at_risk),
        "high_risk_lot_count": len(high_risk),
        "defect_rate": round(failed_wafers / total_wafers, 4) if total_wafers else 0.0,
        "equipment_warnings": equipment_repo.count({"status": {"$ne": "operational"}}),
        "top_root_causes": top_root_causes,
        "yield_trend": _trend_by_week(completed, "yield"),
        "risk_trend": _trend_by_week([l for l in all_lots if l.get("risk_probability") is not None], "risk_probability"),
        "recent_alerts": alert_repo.recent(limit=10),
        "top_risk_lots": lot_repo.top_risk_lots(limit=10),
        "total_lots": len(all_lots),
    }
