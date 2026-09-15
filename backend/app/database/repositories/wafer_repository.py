from typing import List, Optional

from app.database.repositories.base_repository import BaseRepository


class WaferRepository(BaseRepository):
    collection_name = "wafers"

    def get_by_wafer_id(self, wafer_id: str) -> Optional[dict]:
        return self.find_one({"wafer_id": wafer_id})

    def by_lot(self, lot_id: str) -> List[dict]:
        return self.find_many({"lot_id": lot_id}, sort=[("slot", 1)])

    def random_sample(self, limit: int = 20) -> List[dict]:
        return [
            doc
            for doc in self.collection.aggregate([{"$sample": {"size": limit}}])
        ]
