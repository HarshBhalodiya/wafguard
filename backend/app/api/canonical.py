"""
Canonical routes named in the brief (Phase 9): /api/predict, /api/root-cause,
/api/defects/lots. They reuse the existing handlers, so the older paths
(/api/yield/predict, /api/lots/{id}/root-cause, /api/lots/{id}/evidence-graph)
keep working unchanged while the new UI moves to these names.

Brief route map:  /api/predict · /api/lot-risk · /api/root-cause · /api/similarity
/api/defects · /api/recommendations · /api/whatif · /api/quality  (+ /api/jobs)
"""
from fastapi import APIRouter, Depends, Query
from pymongo.database import Database

from app.api import rootcause as rootcause_routes
from app.api import yield_prediction
from app.api.deps import get_db
from app.database.repositories.lot_repository import LotRepository
from app.schemas.prediction import YieldPredictRequest
from app.utils.exceptions import NotFoundError
from app.utils.responses import success

router = APIRouter()


@router.post("/predict")
def predict(payload: YieldPredictRequest, db: Database = Depends(get_db)):
    """Yield + risk prediction for a stored lot or a feature payload (same as /api/yield/predict)."""
    return yield_prediction.predict_yield(payload, db)


@router.get("/root-cause/{lot_id}")
def root_cause(lot_id: str, refresh: bool = Query(default=False), db: Database = Depends(get_db)):
    """Ranked root-cause candidates with evidence (same as /api/lots/{lot_id}/root-cause)."""
    return rootcause_routes.get_root_cause(lot_id, refresh, db)


@router.get("/root-cause/{lot_id}/graph")
def root_cause_graph(lot_id: str, top_n: int = Query(default=3, ge=1, le=10), db: Database = Depends(get_db)):
    """Root-cause evidence graph (same as /api/lots/{lot_id}/evidence-graph)."""
    return rootcause_routes.get_evidence_graph(lot_id, top_n, db)


@router.get("/defects/lots/{lot_id}")
def lot_defects(lot_id: str, db: Database = Depends(get_db)):
    """Defect summary for one lot: inspection pattern, density, counts by type/severity, wafer fail count."""
    lot = LotRepository(db).get_by_lot_id(lot_id)
    if not lot:
        raise NotFoundError(f"Lot {lot_id} was not found.")
    # One indexed find (lot_id index) + counting here, rather than two $group
    # aggregations: same result, one round-trip, and cheap on the in-memory test DB.
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for d in db.defects.find({"lot_id": lot_id}, {"_id": 0, "defect_type": 1, "severity": 1}):
        by_type[d.get("defect_type")] = by_type.get(d.get("defect_type"), 0) + 1
        by_severity[d.get("severity")] = by_severity.get(d.get("severity"), 0) + 1
    wafers = list(db.wafers.find({"lot_id": lot_id}, {"_id": 0, "wafer_id": 1, "yield_status": 1, "wafer_map_pattern": 1}))
    return success(
        {
            "lot_id": lot_id,
            "inspection_pattern": lot.get("spatial_signature"),
            "defect_density": lot.get("defect_density"),
            "n_defects": sum(by_type.values()),
            "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
            "by_severity": by_severity,
            "n_wafers": len(wafers),
            "n_failed_wafers": sum(1 for w in wafers if w.get("yield_status") == "fail"),
            "classified_patterns": {
                p: sum(1 for w in wafers if w.get("wafer_map_pattern") == p)
                for p in sorted({w.get("wafer_map_pattern") for w in wafers if w.get("wafer_map_pattern")})
            },
            "label": "SYNTHETIC inspection data (the Mongo defect table may be capped per lot; see seed notes).",
        }
    )
