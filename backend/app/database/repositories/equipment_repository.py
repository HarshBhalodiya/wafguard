from typing import List, Optional

from app.database.repositories.base_repository import BaseRepository


class EquipmentRepository(BaseRepository):
    collection_name = "equipment"

    def get_by_equipment_id(self, equipment_id: str) -> Optional[dict]:
        return self.find_one({"equipment_id": equipment_id})

    def all(self) -> List[dict]:
        return self.find_many({}, sort=[("equipment_id", 1)])

    def peers(self, tool_family: str, exclude_equipment_id: str) -> List[dict]:
        return self.find_many({"tool_family": tool_family, "equipment_id": {"$ne": exclude_equipment_id}})
