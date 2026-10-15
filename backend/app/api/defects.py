from fastapi import APIRouter, Depends, File, UploadFile

from app.services import defect_service
from app.utils.responses import success
from app.utils.security import require_roles

router = APIRouter()


@router.post("/defects/analyze")
async def analyze_defect(
    file: UploadFile = File(...),
    _actor: dict = Depends(require_roles("process_engineer", "equipment_engineer", "admin")),
):
    content = await file.read()
    result = defect_service.analyze_uploaded_image(content, filename=file.filename)
    return success(result)
