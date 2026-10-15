from fastapi import APIRouter, Depends
from pymongo.database import Database

from app.api.deps import get_db
from app.database.repositories.equipment_repository import EquipmentRepository
from app.database.repositories.lot_repository import LotRepository
from app.database.repositories.maintenance_repository import MaintenanceRepository
from app.services import control_chart_service
from app.utils.exceptions import NotFoundError
from app.utils.responses import success

router = APIRouter()


@router.get("/equipment")
def list_equipment(db: Database = Depends(get_db)):
    return success(EquipmentRepository(db).all())


@router.get("/equipment/{equipment_id}")
def get_equipment(equipment_id: str, db: Database = Depends(get_db)):
    equipment_repo = EquipmentRepository(db)
    equipment = equipment_repo.get_by_equipment_id(equipment_id)
    if not equipment:
        raise NotFoundError(f"Equipment {equipment_id} was not found.")

    maintenance_repo = MaintenanceRepository(db)
    lot_repo = LotRepository(db)

    return success(
        {
            **equipment,
            "recent_maintenance": maintenance_repo.by_equipment(equipment_id, limit=10),
            "recent_lots": lot_repo.find_many({"equipment": equipment_id}, sort=[("process_start", -1)], limit=10),
            "days_since_last_maintenance": maintenance_repo.days_since_last(equipment_id),
            "peers": equipment_repo.peers(equipment["tool_family"], equipment_id),
        }
    )


@router.get("/equipment/{equipment_id}/control-chart")
def get_control_chart(
    equipment_id: str,
    parameter: str = "rf_power",
    limit: int = 100,
    db: Database = Depends(get_db),
):
    """Lot-level SPC chart for one tracked parameter on one tool (Phase 5, P5-2).
    Centerline/sigma come from the tool's own prior history; each point's z-score
    is judged only against points before it."""
    if not EquipmentRepository(db).get_by_equipment_id(equipment_id):
        raise NotFoundError(f"Equipment {equipment_id} was not found.")
    return success(control_chart_service.compute_control_chart(db, equipment_id, parameter, limit=limit))
