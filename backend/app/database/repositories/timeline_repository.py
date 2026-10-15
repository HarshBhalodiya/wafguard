from typing import List, Optional

from app.database.repositories.base_repository import BaseRepository


class TimelineEventRepository(BaseRepository):
    collection_name = "timeline_events"

    def for_lot(self, lot_id: str, limit: int = 500) -> List[dict]:
        return self.find_many({"lot_id": lot_id}, sort=[("ts", 1)], limit=limit)

    def for_equipment(self, equipment_id: str, limit: int = 500) -> List[dict]:
        return self.find_many({"equipment_id": equipment_id}, sort=[("ts", 1)], limit=limit)

    def for_lot_by_type(self, lot_id: str, event_type: str) -> List[dict]:
        return self.find_many({"lot_id": lot_id, "event_type": event_type}, sort=[("ts", 1)])
