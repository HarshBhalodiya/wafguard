from typing import List, Optional

from app.database.repositories.base_repository import BaseRepository


class PredictionRepository(BaseRepository):
    collection_name = "predictions"

    def latest_for_lot(self, lot_id: str) -> Optional[dict]:
        docs = self.find_many({"lot_id": lot_id}, sort=[("timestamp", -1)], limit=1)
        return docs[0] if docs else None

    def history_for_lot(self, lot_id: str) -> List[dict]:
        return self.find_many({"lot_id": lot_id}, sort=[("timestamp", 1)])

    def for_equipment(self, equipment_id: str, exclude_lot_id: Optional[str] = None, limit: int = 30) -> List[dict]:
        query: dict = {"features_snapshot.equipment": equipment_id}
        if exclude_lot_id:
            query["lot_id"] = {"$ne": exclude_lot_id}
        return self.find_many(query, sort=[("timestamp", -1)], limit=limit)
