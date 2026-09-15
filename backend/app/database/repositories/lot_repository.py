from typing import Any, Dict, List, Optional

from app.database.repositories.base_repository import BaseRepository, strip_id


class LotRepository(BaseRepository):
    collection_name = "lots"

    def get_by_lot_id(self, lot_id: str) -> Optional[dict]:
        return self.find_one({"lot_id": lot_id})

    def search(
        self,
        *,
        query_text: Optional[str] = None,
        status: Optional[str] = None,
        risk_level: Optional[str] = None,
        equipment: Optional[str] = None,
        sort_by: str = "process_start",
        sort_dir: int = -1,
        skip: int = 0,
        limit: int = 20,
    ) -> Dict[str, Any]:
        mongo_query: Dict[str, Any] = {}
        if query_text:
            mongo_query["$or"] = [
                {"lot_id": {"$regex": query_text, "$options": "i"}},
                {"product": {"$regex": query_text, "$options": "i"}},
                {"equipment": {"$regex": query_text, "$options": "i"}},
            ]
        if status:
            mongo_query["status"] = status
        if risk_level:
            mongo_query["risk_level"] = risk_level
        if equipment:
            mongo_query["equipment"] = equipment

        total = self.count(mongo_query)
        items = self.find_many(mongo_query, sort=[(sort_by, sort_dir)], skip=skip, limit=limit)
        return {"items": items, "total": total, "skip": skip, "limit": limit}

    def top_risk_lots(self, limit: int = 10) -> List[dict]:
        return self.find_many({"risk_level": {"$in": ["HIGH", "MEDIUM"]}}, sort=[("risk_probability", -1)], limit=limit)

    def update_prediction(self, lot_id: str, fields: Dict[str, Any]) -> None:
        self.update_one({"lot_id": lot_id}, fields)

    def distinct_equipment(self) -> List[str]:
        return sorted(self.collection.distinct("equipment"))
