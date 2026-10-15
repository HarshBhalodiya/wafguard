from pydantic import BaseModel, Field


class CounterfactualRequest(BaseModel):
    """Phase 6 counterfactual request. Allowed keys and limits: GET /api/whatif/limits."""

    lot_id: str = Field(min_length=1)
    changes: dict[str, float] = Field(description='e.g. {"rf_power": 858, "maintenance_days_since_last": 0}')
