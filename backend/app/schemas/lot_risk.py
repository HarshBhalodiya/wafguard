from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PlannedRunRequest(BaseModel):
    """A run that has not happened yet (brief PHASE 4).

    Only pre-run facts can be supplied: which tool, when it is planned, and the
    recipe/product/step. There is deliberately no field for telemetry or for the
    lot's own outcome — those do not exist before the run.
    """

    equipment_id: str = Field(min_length=1, description="Tool the run is planned on, e.g. ETCH_007")
    planned_start: Optional[datetime] = Field(
        default=None,
        description="When the run is planned. Defaults to the dataset's latest run time ('now' for this dataset).",
    )
    product: Optional[str] = Field(default=None, description="Product to run; defaults to the tool's last product.")
    recipe_id: Optional[str] = Field(default=None, description="Recipe to run; defaults to the tool's last recipe.")
    process_step: Optional[str] = Field(default=None, description="Process step; defaults to the tool's last step.")
