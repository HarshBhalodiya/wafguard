from fastapi import APIRouter, Depends
from pymongo.database import Database

from app.api.deps import get_db
from app.copilot import service as copilot_v2
from app.schemas.copilot import CopilotAskRequest, CopilotRequest
from app.services import copilot_service
from app.utils.responses import success
from app.utils.security import require_roles

router = APIRouter()
_COPILOT_ROLES = ("process_engineer", "yield_engineer", "equipment_engineer", "admin")


@router.post("/lots/{lot_id}/copilot")
def ask_copilot(
    lot_id: str,
    payload: CopilotRequest,
    db: Database = Depends(get_db),
    _actor: dict = Depends(require_roles(*_COPILOT_ROLES)),
):
    result = copilot_service.answer_question(db, lot_id, payload.question or "")
    return success(result)


@router.post("/copilot/ask")
def ask(
    payload: CopilotAskRequest,
    db: Database = Depends(get_db),
    _actor: dict = Depends(require_roles(*_COPILOT_ROLES)),
):
    """Phase 8: retrieval-first answer where every sentence cites evidence ids."""
    return success(copilot_v2.answer(db, payload.question, lot_id=payload.lot_id))
