from typing import Optional

from pydantic import BaseModel, Field


class CopilotRequest(BaseModel):
    question: Optional[str] = None


class CopilotAskRequest(BaseModel):
    """Phase 8: free-form question; the lot can be named in the question or given explicitly."""

    question: str = Field(min_length=1, max_length=1000)
    lot_id: Optional[str] = None
