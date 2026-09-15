from fastapi import APIRouter, Depends
from pymongo.database import Database

from app.api.deps import get_db
from app.database.repositories.equipment_repository import EquipmentRepository
from app.database.repositories.lot_repository import LotRepository
from app.database.repositories.prediction_repository import PredictionRepository
from app.schemas.prediction import YieldPredictRequest
from app.services import yield_service
from app.utils.exceptions import NotFoundError, ValidationFailedError
from app.utils.responses import success
from app.utils.time import utc_now

router = APIRouter()


@router.post("/yield/predict")
def predict_yield(payload: YieldPredictRequest, db: Database = Depends(get_db)):
    lot_repo = LotRepository(db)
    equipment_repo = EquipmentRepository(db)
    prediction_repo = PredictionRepository(db)

    if payload.lot_id:
        lot = lot_repo.get_by_lot_id(payload.lot_id)
        if not lot:
            raise NotFoundError(f"Lot {payload.lot_id} was not found.")
        latest = prediction_repo.latest_for_lot(payload.lot_id)
        if not latest:
            raise NotFoundError(
                f"No feature snapshot stored yet for {payload.lot_id}. "
                "Run scripts/train_yield_model.py (it backfills every lot)."
            )
        feature_snapshot = latest["features_snapshot"]
        equipment_doc = equipment_repo.get_by_equipment_id(lot["equipment"])
        recent_yields = yield_service.get_recent_equipment_yields(db, lot["equipment"], exclude_lot_id=payload.lot_id)
    elif payload.features:
        feature_snapshot = payload.features
        equipment_id = feature_snapshot.get("equipment")
        equipment_doc = equipment_repo.get_by_equipment_id(equipment_id) if equipment_id else None
        recent_yields = yield_service.get_recent_equipment_yields(db, equipment_id) if equipment_id else []
    else:
        raise ValidationFailedError("Provide either 'lot_id' or 'features' in the request body.")

    result = yield_service.predict_lot(feature_snapshot, equipment_doc, recent_yields)

    if payload.lot_id:
        lot_repo.update_prediction(
            payload.lot_id,
            {
                "predicted_yield": result["predicted_yield"],
                "risk_probability": result["risk_probability"],
                "risk_level": result["risk_level"],
            },
        )
        prediction_repo.insert_one(
            {
                "lot_id": payload.lot_id,
                "model_name": result["model_name"],
                "model_version": result["model_version"],
                "predicted_yield": result["predicted_yield"],
                "risk_probability": result["risk_probability"],
                "risk_level": result["risk_level"],
                "features_snapshot": feature_snapshot,
                "risk_breakdown": result["risk_breakdown"],
                "timestamp": utc_now(),
            }
        )

    return success(
        {
            "lot_id": payload.lot_id,
            "predicted_yield": result["predicted_yield"],
            "risk_probability": result["risk_probability"],
            "risk_level": result["risk_level"],
        }
    )
