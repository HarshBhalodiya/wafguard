"""
Generic repository wrapping a single MongoDB collection.

Centralizing Mongo access here means API routes and services never import
pymongo directly (spec section 26: "Create repository/service abstractions
instead of putting database logic inside API routes").
"""
from typing import Any, Dict, List, Optional

from pymongo.collection import Collection
from pymongo.database import Database


def strip_id(document: Optional[dict]) -> Optional[dict]:
    """Remove Mongo's internal ObjectId before a document goes into an API response."""
    if document is None:
        return None
    document = dict(document)
    document.pop("_id", None)
    return document


class BaseRepository:
    collection_name: str = ""

    def __init__(self, db: Database):
        self.db = db
        self.collection: Collection = db[self.collection_name]

    def find_one(self, query: Dict[str, Any]) -> Optional[dict]:
        return strip_id(self.collection.find_one(query))

    def find_many(
        self,
        query: Optional[Dict[str, Any]] = None,
        *,
        sort: Optional[List[tuple]] = None,
        skip: int = 0,
        limit: int = 0,
    ) -> List[dict]:
        cursor = self.collection.find(query or {})
        if sort:
            cursor = cursor.sort(sort)
        if skip:
            cursor = cursor.skip(skip)
        if limit:
            cursor = cursor.limit(limit)
        return [strip_id(doc) for doc in cursor]

    def count(self, query: Optional[Dict[str, Any]] = None) -> int:
        return self.collection.count_documents(query or {})

    def insert_one(self, document: Dict[str, Any]) -> None:
        self.collection.insert_one(document)

    def insert_many(self, documents: List[Dict[str, Any]]) -> None:
        if documents:
            self.collection.insert_many(documents)

    def update_one(self, query: Dict[str, Any], update: Dict[str, Any], upsert: bool = False) -> None:
        self.collection.update_one(query, {"$set": update}, upsert=upsert)

    def delete_many(self, query: Optional[Dict[str, Any]] = None) -> int:
        result = self.collection.delete_many(query or {})
        return result.deleted_count
