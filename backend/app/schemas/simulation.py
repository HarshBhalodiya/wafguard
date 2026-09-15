from pydantic import BaseModel, Field


class WhatIfRequest(BaseModel):
    lot_id: str
    overrides: dict[str, float] = Field(description="e.g. {\"rf_power\": 858} — see /api/what-if for allowed keys.")
