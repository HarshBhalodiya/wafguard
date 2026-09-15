from fastapi import APIRouter, Depends
from pymongo.database import Database

from app.api.deps import get_db
from app.services.dashboard_service import get_dashboard_summary
from app.utils.responses import success

router = APIRouter()


@router.get("/dashboard")
def dashboard(db: Database = Depends(get_db)):
    return success(get_dashboard_summary(db))
