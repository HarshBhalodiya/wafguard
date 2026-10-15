from fastapi import APIRouter, Depends
from pymongo.database import Database

from app.api.deps import get_db
from app.schemas.whatif import CounterfactualRequest
from app.utils.responses import success
from app.utils.security import require_roles
from app.whatif import counterfactual

router = APIRouter()


@router.get("/whatif/limits")
def get_whatif_limits():
    """Which parameters can be changed, and the recipe/physical limits that block a change."""
    return success({"label": counterfactual.LABEL, **counterfactual.load_limits()})


@router.post("/whatif")
def post_whatif(
    payload: CounterfactualRequest,
    db: Database = Depends(get_db),
    _actor: dict = Depends(require_roles("process_engineer", "yield_engineer", "admin")),
):
    """Model-based counterfactual with uncertainty ranges. Read-only: persists nothing."""
    return success(counterfactual.run_counterfactual(db, payload.lot_id, payload.changes))
