from fastapi import APIRouter, Depends, Query
from pymongo.database import Database

from app.api.deps import get_db
from app.services import recommendation_service
from app.utils.responses import success

router = APIRouter()


@router.get("/lots/{lot_id}/recommendations")
def get_recommendations(lot_id: str, refresh: bool = Query(default=False), db: Database = Depends(get_db)):
    result = recommendation_service.generate_recommendations(db, lot_id, force_recompute=refresh)
    return success(result)
