from typing import Optional

from pydantic import BaseModel


class CopilotRequest(BaseModel):
    question: Optional[str] = None
