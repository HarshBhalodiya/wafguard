from fastapi import APIRouter, Depends, Query
from pymongo.database import Database

from app.api.deps import get_db
from app.database.repositories.wafer_repository import WaferRepository
from app.services import defect_service
from app.utils.exceptions import NotFoundError
from app.utils.responses import success

router = APIRouter()


@router.get("/wafers")
def list_wafers(lot_id: str = Query(...), db: Database = Depends(get_db)):
    return success(WaferRepository(db).by_lot(lot_id))


@router.get("/wafers/{wafer_id}/map")
def get_wafer_map(wafer_id: str, classify: bool = Query(default=True), db: Database = Depends(get_db)):
    wafer_repo = WaferRepository(db)
    wafer = wafer_repo.get_by_wafer_id(wafer_id)
    if not wafer:
        raise NotFoundError(f"Wafer {wafer_id} was not found.")

    if classify and not wafer.get("wafer_map_pattern"):
        return success(defect_service.classify_wafer_by_id(db, wafer_id))

    return success(wafer)
