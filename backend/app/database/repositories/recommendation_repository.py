from typing import Optional

from app.database.repositories.base_repository import BaseRepository


class RecommendationRepository(BaseRepository):
    collection_name = "recommendations"

    def latest_for_lot(self, lot_id: str) -> Optional[dict]:
        docs = self.find_many({"lot_id": lot_id}, sort=[("timestamp", -1)], limit=1)
        return docs[0] if docs else None

    def upsert_for_lot(self, lot_id: str, document: dict) -> None:
        self.update_one({"lot_id": lot_id}, document, upsert=True)
