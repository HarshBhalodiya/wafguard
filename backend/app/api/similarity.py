from fastapi import APIRouter, Depends, Query
from pymongo.database import Database

from app.api.deps import get_db
from app.services import similarity_service
from app.utils.responses import success

router = APIRouter()


@router.get("/similar/{lot_id}")
def get_similar_lots(lot_id: str, k: int = Query(default=5, ge=1, le=20), db: Database = Depends(get_db)):
    return success(similarity_service.find_similar_lots(db, lot_id, k=k))
