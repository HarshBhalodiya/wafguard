from typing import Optional

from fastapi import APIRouter, Depends, Query
from pymongo.database import Database

from app.api.deps import get_db
from app.database.repositories.lot_repository import LotRepository
from app.utils.responses import success

router = APIRouter()


@router.get("/risk/lots")
def get_risk_lots(
    risk_level: Optional[str] = Query(default=None, description="LOW | MEDIUM | HIGH"),
    limit: int = Query(default=50, ge=1, le=500),
    db: Database = Depends(get_db),
):
    query = {"risk_level": risk_level} if risk_level else {"risk_level": {"$in": ["MEDIUM", "HIGH"]}}
    lots = LotRepository(db).find_many(query, sort=[("risk_probability", -1)], limit=limit)
    return success(lots)
