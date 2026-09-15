from datetime import datetime, timezone
from typing import List

from app.database.repositories.base_repository import BaseRepository
from app.utils.time import utc_now


class MaintenanceRepository(BaseRepository):
    collection_name = "maintenance"

    def by_equipment(self, equipment_id: str, limit: int = 20) -> List[dict]:
        return self.find_many({"equipment_id": equipment_id}, sort=[("timestamp", -1)], limit=limit)

    def days_since_last(self, equipment_id: str) -> float | None:
        docs = self.by_equipment(equipment_id, limit=1)
        if not docs:
            return None

        last = docs[0]["timestamp"]
        if isinstance(last, str):
            last = datetime.fromisoformat(last)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (utc_now() - last).days
