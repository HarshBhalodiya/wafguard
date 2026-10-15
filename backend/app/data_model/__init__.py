"""
Typed data model for WafGuard manufacturing entities (Pydantic v2).
See app/data_model/entities.py for the schemas.
"""
from app.data_model.entities import (
    Chamber,
    Defect,
    ElectricalTestResult,
    Equipment,
    Lot,
    MaintenanceEvent,
    ProcessStep,
    Recipe,
    SensorTelemetry,
    TimelineEvent,
    Wafer,
    WaferMap,
)

__all__ = [
    "Chamber",
    "Defect",
    "ElectricalTestResult",
    "Equipment",
    "Lot",
    "MaintenanceEvent",
    "ProcessStep",
    "Recipe",
    "SensorTelemetry",
    "TimelineEvent",
    "Wafer",
    "WaferMap",
]
