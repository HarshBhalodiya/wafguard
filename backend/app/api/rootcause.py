from fastapi import APIRouter, Depends, Query
from pymongo.database import Database

from app.api.deps import get_db
from app.database.repositories.rootcause_repository import RootCauseRepository
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
