from fastapi import APIRouter, Depends
from pymongo.database import Database

from app.api.deps import get_db
from app.schemas.copilot import CopilotRequest
from app.services import copilot_service
from app.utils.responses import success

router = APIRouter()


@router.post("/lots/{lot_id}/copilot")
def ask_copilot(lot_id: str, payload: CopilotRequest, db: Database = Depends(get_db)):
    result = copilot_service.answer_question(db, lot_id, payload.question or "")
    return success(result)
