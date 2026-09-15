from typing import Optional

from pydantic import BaseModel, Field


class YieldPredictRequest(BaseModel):
    lot_id: Optional[str] = Field(default=None, description="Predict using this existing lot's stored feature snapshot.")
    features: Optional[dict] = Field(default=None, description="Or supply a raw feature payload directly (advanced/testing use).")
