from typing import Optional

from fastapi import APIRouter, Depends, Query
from pymongo.database import Database

from app.api.deps import get_db
from app.database.repositories.lot_repository import LotRepository
from app.utils.exceptions import NotFoundError
from app.utils.responses import success

router = APIRouter()


@router.get("/lots")
def list_lots(
    q: Optional[str] = Query(default=None, description="Search lot_id / product / equipment"),
    status: Optional[str] = None,
    risk_level: Optional[str] = None,
    equipment: Optional[str] = None,
    sort_by: str = "process_start",
    sort_dir: int = -1,
    skip: int = Query(default=0, ge=0),
    # Ceiling of 2000 comfortably covers the whole seeded dataset (950 lots) so
    # pages like Yield Analytics can pull everything for client-side
    # aggregation without a dedicated server-side rollup endpoint — this
    # prototype's lot count is intentionally small enough for that to be fine.
    limit: int = Query(default=20, ge=1, le=2000),
    db: Database = Depends(get_db),
):
    repo = LotRepository(db)
    result = repo.search(
        query_text=q,
        status=status,
        risk_level=risk_level,
        equipment=equipment,
        sort_by=sort_by,
        sort_dir=1 if sort_dir == 1 else -1,
        skip=skip,
        limit=limit,
    )
    return success(result)


@router.get("/lots/{lot_id}")
def get_lot(lot_id: str, db: Database = Depends(get_db)):
    lot = LotRepository(db).get_by_lot_id(lot_id)
    if not lot:
        raise NotFoundError(f"Lot {lot_id} was not found.")
    return success(lot)
