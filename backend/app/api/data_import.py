from fastapi import APIRouter, Depends
from pymongo.database import Database

from app.api.deps import get_db
from app.database.repositories.lot_repository import LotRepository
from app.schemas.data_import import DataImportRequest
from app.utils.responses import success
from app.utils.security import require_roles

router = APIRouter()


@router.post("/data/import")
def import_lots(
    payload: DataImportRequest,
    db: Database = Depends(get_db),
    _user: dict = Depends(require_roles("admin", "process_engineer")),
):
    lot_repo = LotRepository(db)
    imported = 0
    for record in payload.lots:
        doc = record.model_dump(by_alias=True)
        doc["process_start"] = doc["process_start"].isoformat()
        doc.setdefault("predicted_yield", None)
        doc.setdefault("risk_probability", None)
        doc.setdefault("risk_level", None)
        doc["data_source"] = "imported"
        lot_repo.update_one({"lot_id": doc["lot_id"]}, doc, upsert=True)
        imported += 1
    return success({"imported": imported})
