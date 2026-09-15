"""
What-if simulator (spec section 22).

Re-runs Model B on a lot's feature snapshot with user-supplied parameter
overrides. Explicitly labeled "model-based what-if simulation" — this is
a re-inference exercise, not a physical guarantee, and it NEVER persists
anything or touches equipment/process state (spec: "must not allow the
simulator to write changes to a real manufacturing system").
"""
from app.database.repositories.equipment_repository import EquipmentRepository
from app.database.repositories.lot_repository import LotRepository
from app.database.repositories.prediction_repository import PredictionRepository
from app.services import yield_service
from app.utils.exceptions import NotFoundError, ValidationFailedError

# Parameters an engineer is allowed to hypothetically change. Anything else
# (equipment, process_step, historical features) stays fixed for the scenario.
OVERRIDABLE_PARAMETERS = ["temperature", "pressure", "rf_power", "gas_flow", "etch_time", "voltage", "current"]


def run_what_if(db, lot_id: str, overrides: dict) -> dict:
    unknown = set(overrides) - set(OVERRIDABLE_PARAMETERS)
    if unknown:
        raise ValidationFailedError(f"Cannot override unknown parameter(s): {sorted(unknown)}. Allowed: {OVERRIDABLE_PARAMETERS}")

    lot_repo = LotRepository(db)
    equipment_repo = EquipmentRepository(db)
    prediction_repo = PredictionRepository(db)

    lot = lot_repo.get_by_lot_id(lot_id)
    if not lot:
        raise NotFoundError(f"Lot {lot_id} was not found.")

    prediction = prediction_repo.latest_for_lot(lot_id)
    if not prediction:
        raise NotFoundError(f"No prediction found for lot {lot_id} yet — nothing to simulate against.")

    base_snapshot = dict(prediction["features_snapshot"])
    equipment_doc = equipment_repo.get_by_equipment_id(lot["equipment"])

    recent_yields = yield_service.get_recent_equipment_yields(db, lot["equipment"], exclude_lot_id=lot_id)

    def _apply(snapshot: dict, params: dict) -> dict:
        applied = dict(snapshot)
        for name, value in params.items():
            if f"{name}_mean" in applied:
                applied[f"{name}_mean"] = value
            else:
                applied[name] = value
        return applied

    before_snapshot = base_snapshot
    after_snapshot = _apply(base_snapshot, overrides)

    before = yield_service.predict_lot(before_snapshot, equipment_doc, recent_yields)
    after = yield_service.predict_lot(after_snapshot, equipment_doc, recent_yields)

    return {
        "lot_id": lot_id,
        "label": "Model-based what-if simulation — not a guarantee of physical outcome.",
        "overrides": overrides,
        "before": {
            "predicted_yield": before["predicted_yield"],
            "risk_probability": before["risk_probability"],
            "risk_level": before["risk_level"],
        },
        "after": {
            "predicted_yield": after["predicted_yield"],
            "risk_probability": after["risk_probability"],
            "risk_level": after["risk_level"],
        },
        "delta": {
            "predicted_yield": round(after["predicted_yield"] - before["predicted_yield"], 2),
            "risk_probability": round(after["risk_probability"] - before["risk_probability"], 4),
        },
    }
