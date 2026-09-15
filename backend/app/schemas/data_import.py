from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class LotImportRecord(BaseModel):
    lot_id: str
    product: str
    node: str = "N/A"
    wafer_count: int = 25
    process_start: datetime
    equipment: str
    process_step: str
    status: str = "in_progress"
    yield_value: Optional[float] = Field(default=None, alias="yield")
    process_parameters: Optional[dict] = None

    model_config = {"populate_by_name": True}


class DataImportRequest(BaseModel):
    lots: list[LotImportRecord]
