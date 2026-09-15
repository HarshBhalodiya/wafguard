from fastapi import APIRouter, Depends
from pymongo.database import Database

from app.api.deps import get_db
from app.database.repositories.model_version_repository import ModelVersionRepository
from app.utils.responses import success

router = APIRouter()


@router.get("/models")
def list_models(db: Database = Depends(get_db)):
    models = ModelVersionRepository(db).all_latest()
    trimmed = [
        {
            "model_name": m["model_name"],
            "version": m["version"],
            "task": m.get("task"),
            "dataset": m.get("dataset"),
            "trained_at": m.get("trained_at"),
        }
        for m in models
    ]
    return success(trimmed)


@router.get("/model-performance")
def model_performance(db: Database = Depends(get_db)):
    return success({"models": ModelVersionRepository(db).all_latest()})
