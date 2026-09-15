from typing import List

from app.database.repositories.base_repository import BaseRepository


class DefectRepository(BaseRepository):
    collection_name = "defects"

    def by_wafer(self, wafer_id: str) -> List[dict]:
        return self.find_many({"wafer_id": wafer_id})

    def by_type_counts(self) -> List[dict]:
        return list(
            self.collection.aggregate(
                [
                    {"$group": {"_id": "$defect_type", "count": {"$sum": 1}}},
                    {"$sort": {"count": -1}},
                ]
            )
        )
