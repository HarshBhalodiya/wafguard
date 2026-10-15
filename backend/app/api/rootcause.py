from fastapi import APIRouter, Depends, Query
from pymongo.database import Database

from app.api.deps import get_db
from app.database.repositories.rootcause_repository import RootCauseRepository
from app.rootcause import evidence_graph
from app.services import rootcause_service
from app.utils.responses import success

router = APIRouter()


@router.get("/lots/{lot_id}/root-cause")
def get_root_cause(lot_id: str, refresh: bool = Query(default=False), db: Database = Depends(get_db)):
    if not refresh:
        cached = RootCauseRepository(db).latest_for_lot(lot_id)
        if cached:
            return success(cached)
    return success(rootcause_service.compute_root_cause(db, lot_id))


@router.get("/lots/{lot_id}/evidence-graph")
def get_evidence_graph(
    lot_id: str,
    top_n: int = Query(default=3, ge=1, le=10, description="How many ranked candidates to include"),
    db: Database = Depends(get_db),
):
    """Root-cause evidence graph (brief PHASE 3C): nodes/edges with strengths and
    evidence links, built from measured evidence plus the readable rules file."""
    return success(evidence_graph.build_evidence_graph(db, lot_id, top_n=top_n))
