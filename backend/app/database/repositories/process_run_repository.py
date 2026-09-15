from typing import List, Optional

from app.database.repositories.base_repository import BaseRepository


class ProcessRunRepository(BaseRepository):
    collection_name = "process_runs"

    def get_by_lot(self, lot_id: str) -> Optional[dict]:
        return self.find_one({"lot_id": lot_id})

    def by_equipment(self, equipment_id: str, limit: int = 100) -> List[dict]:
        return self.find_many({"equipment_id": equipment_id}, sort=[("timestamp", -1)], limit=limit)

    def historical_for_feature(self, equipment_id: str, feature: str) -> List[float]:
        docs = self.find_many({"equipment_id": equipment_id}, sort=[("timestamp", -1)], limit=200)
        return [d[feature] for d in docs if feature in d and d[feature] is not None]
