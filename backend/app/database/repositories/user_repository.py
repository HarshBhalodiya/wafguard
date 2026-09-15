from typing import Optional

from app.database.repositories.base_repository import BaseRepository


class UserRepository(BaseRepository):
    collection_name = "users"

    def get_by_username(self, username: str) -> Optional[dict]:
        return self.find_one({"username": username})
