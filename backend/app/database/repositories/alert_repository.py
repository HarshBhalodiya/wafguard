from typing import List, Optional

from app.database.repositories.base_repository import BaseRepository


class AlertRepository(BaseRepository):
    collection_name = "alerts"

    def recent(self, limit: int = 10, severity: Optional[str] = None) -> List[dict]:
        query = {"severity": severity} if severity else {}
        return self.find_many(query, sort=[("created_at", -1)], limit=limit)
