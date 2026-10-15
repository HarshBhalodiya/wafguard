from fastapi import APIRouter, Depends, Query

from app.api.deps import get_db
from app.risk import upcoming_lot_risk
from app.schemas.lot_risk import PlannedRunRequest
from app.utils.responses import success

router = APIRouter()


@router.post("/lot-risk")
def post_lot_risk(payload: PlannedRunRequest, db=Depends(get_db)):
    """Pre-run risk for a planned run (brief PHASE 4).

    Read-only: nothing is persisted and nothing is written to any real system.
    Uses only information that existed before the planned start.
    """
    return success(
        upcoming_lot_risk.score_planned_run(
            db,
            equipment_id=payload.equipment_id,
            planned_start=payload.planned_start or upcoming_lot_risk.dataset_now(db),
            product=payload.product,
            recipe_id=payload.recipe_id,
            process_step=payload.process_step,
        )
    )


@router.get("/lot-risk/upcoming")
def get_upcoming_lot_risk(limit: int = Query(default=20, ge=1, le=200), db=Depends(get_db)):
    """Risk-sorted "what is about to run" list — one hypothetical next run per tool."""
    return success(upcoming_lot_risk.upcoming_lot_risks(db, limit=limit))
