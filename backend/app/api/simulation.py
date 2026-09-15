from fastapi import APIRouter, Depends
from pymongo.database import Database

from app.api.deps import get_db
from app.schemas.simulation import WhatIfRequest
from app.services import simulation_service
from app.utils.responses import success

router = APIRouter()


@router.post("/what-if")
def what_if(payload: WhatIfRequest, db: Database = Depends(get_db)):
    return success(simulation_service.run_what_if(db, payload.lot_id, payload.overrides))
