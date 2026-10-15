from fastapi import APIRouter, Depends, Query
from pymongo.database import Database

from app.api.deps import get_db
from app.recommend import engine as recommend_engine
from app.services import recommendation_service
from app.utils.responses import success

router = APIRouter()


@router.get("/lots/{lot_id}/recommendations")
def get_recommendations(lot_id: str, refresh: bool = Query(default=False), db: Database = Depends(get_db)):
    result = recommendation_service.generate_recommendations(db, lot_id, force_recompute=refresh)
    return success(result)


@router.get("/recommendations/{lot_id}")
def get_evidence_backed_recommendations(lot_id: str, refresh: bool = Query(default=False), db: Database = Depends(get_db)):
    """Phase 5: knowledge-base actions with measured (synthetic) intervention statistics.
    Read-only — computes from stored records, persists nothing."""
    return success(recommend_engine.recommend_for_lot(db, lot_id, force_recompute=refresh))
