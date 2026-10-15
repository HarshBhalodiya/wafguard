"""
Typed manufacturing data model (Pydantic v2 schemas).

One schema per entity in the target layout: Lot -> Wafer -> Step ->
Chamber -> Equipment, plus telemetry, defects, wafer maps, maintenance,
electrical tests, and timeline events. These are the contracts used by
the seed script, the timeline API, and (from Phase 2 on) feature
engineering and ML inputs.

All data described here is SYNTHETIC in this project (see
`is_simulated`/`data_source` fields).
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

DataSource = Literal["synthetic", "imported", "secom", "wm811k"]
FaultType = Literal[
    "none",
    "chamber_pressure_instability",
    "rf_power_drift",
    "sensor_drift",
    "equipment_degradation",
    "sensor_dropout",
    "recipe_transition_problem",
    "contamination_event",
]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]


class Equipment(BaseModel):
    equipment_id: str
    tool_family: str
    location: Optional[str] = None
    install_date: Optional[str] = None
    health_score: float = Field(ge=0, le=100)
    status: str = "operational"
    anomaly_status: Optional[str] = None
    is_simulated_metadata: bool = True


class Chamber(BaseModel):
    chamber_id: str
    equipment_id: str
    chamber_index: int = Field(ge=1)
    is_simulated_metadata: bool = True


class Recipe(BaseModel):
    recipe_id: str
    process_step: str
    sub_recipe: Optional[str] = None
    version: Optional[str] = None
    is_simulated_metadata: bool = True


class ProcessStep(BaseModel):
    """A step is a (recipe, tool-family) stage a lot passes through."""
    step_id: str
    lot_id: str
    recipe_id: str
    process_step: str
    equipment_id: str
    chamber_id: str
    seq: int = Field(ge=1, description="Order of this step in the lot's route (1 = first).")
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class Lot(BaseModel):
    lot_id: str
    product: str
    recipe_id: str
    process_step: str
    equipment_id: str
    chamber_id: str
    process_start: datetime
    process_end: Optional[datetime] = None
    wafer_count: int = Field(ge=1)
    status: Literal["completed", "in_progress", "scrapped"] = "completed"
    yield_pct: Optional[float] = Field(default=None, ge=0, le=100, serialization_alias="yield")
    is_low_yield: bool = False
    defect_probability: Optional[float] = Field(default=None, ge=0, le=1)
    defect_density: Optional[float] = Field(default=None, ge=0)
    spatial_signature: Optional[str] = None
    equipment_days_since_pm: Optional[float] = Field(default=None, ge=0)
    predicted_yield: Optional[float] = None
    risk_probability: Optional[float] = None
    risk_level: Optional[RiskLevel] = None
    is_simulated: bool = True
    data_source: DataSource = "synthetic"


class Wafer(BaseModel):
    wafer_id: str
    lot_id: str
    slot: int = Field(ge=1)
    yield_status: Literal["pass", "fail"]
    true_pattern: Optional[str] = None
    defect_density: Optional[float] = None
    electrical_pass_rate: Optional[float] = Field(default=None, ge=0, le=1)
    pattern_source: Optional[str] = None
    wafer_map_pattern: Optional[str] = None
    wafer_map_confidence: Optional[float] = None


class SensorTelemetry(BaseModel):
    """One time-series sample. Stored in Parquet; not a Mongo document."""
    lot_id: str
    equipment_id: str
    chamber_id: Optional[str] = None
    parameter: str
    ts_index: int = Field(ge=0)
    timestamp: datetime
    value: Optional[float] = None  # None = missing sample (dropout)


class Defect(BaseModel):
    defect_id: str
    wafer_id: str
    lot_id: str
    defect_type: str
    severity: Literal["LOW", "MEDIUM", "HIGH"]
    x: float = Field(ge=-100, le=100)
    y: float = Field(ge=-100, le=100)
    size: float = Field(gt=0)
    timestamp: Optional[datetime] = None


class WaferMap(BaseModel):
    """Die-status grid summary. The raw grid itself is materialized on demand."""
    wafer_id: str
    lot_id: str
    grid_size: int = 48
    pattern_source: Optional[str] = None
    defect_density: Optional[float] = None


class MaintenanceEvent(BaseModel):
    event_id: str
    equipment_id: str
    chamber_id: Optional[str] = None
    event_type: Literal["scheduled_pm", "calibration", "part_replacement", "unscheduled_repair"]
    timestamp: datetime
    notes: Optional[str] = None


class ElectricalTestResult(BaseModel):
    """Per-lot end-of-line electrical measurements (synthetic)."""
    lot_id: str
    vbd_mean: Optional[float] = None  # breakdown voltage
    leak_mean: Optional[float] = None  # leakage current
    rsq_mean: Optional[float] = None  # sheet resistance
    pass_rate: Optional[float] = Field(default=None, ge=0, le=1)


class TimelineEvent(BaseModel):
    """One entry in a lot/equipment's ordered event stream."""
    event_id: Optional[str] = None
    lot_id: Optional[str] = None
    equipment_id: Optional[str] = None
    chamber_id: Optional[str] = None
    recipe_id: Optional[str] = None
    event_type: Literal[
        "PROCESS_START", "PROCESS_END", "PARAMETER_EXCURSION", "EQUIPMENT_ALARM",
        "MAINTENANCE", "RECIPE_TRANSITION", "INSPECTION",
    ]
    ts: datetime
    payload: dict = Field(default_factory=dict)
