from fastapi import APIRouter, File, UploadFile

from app.services import defect_service
from app.utils.responses import success

router = APIRouter()


@router.post("/defects/analyze")
async def analyze_defect(file: UploadFile = File(...)):
    content = await file.read()
    result = defect_service.analyze_uploaded_image(content, filename=file.filename)
    return success(result)
