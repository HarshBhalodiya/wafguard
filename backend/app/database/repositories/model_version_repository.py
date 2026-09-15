from typing import List, Optional

from app.database.repositories.base_repository import BaseRepository


class ModelVersionRepository(BaseRepository):
    collection_name = "model_versions"

    def latest(self, model_name: str) -> Optional[dict]:
        docs = self.find_many({"model_name": model_name}, sort=[("trained_at", -1)], limit=1)
        return docs[0] if docs else None

    def all_latest(self) -> List[dict]:
        names = self.collection.distinct("model_name")
        return [self.latest(name) for name in names if self.latest(name)]
