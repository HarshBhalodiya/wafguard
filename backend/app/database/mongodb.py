"""
MongoDB connection management.

The app must be able to start even when MongoDB is not reachable yet
(spec section 41 / 4) — so connecting is lazy and non-fatal. Any endpoint
that actually needs the database calls `get_database()`, which raises a
`DatabaseUnavailableError` (turned into a clean 503 envelope) only at the
point of use, rather than crashing the whole process at import time.
"""
from typing import Optional

from pymongo import ASCENDING, MongoClient
from pymongo.database import Database
from pymongo.errors import PyMongoError

from app.config import get_settings
from app.utils.exceptions import DatabaseUnavailableError
from app.utils.logging import get_logger

logger = get_logger(__name__)

_client: Optional[MongoClient] = None


def connect() -> None:
    """Attempt to create the Mongo client. Never raises — logs and moves on."""
    global _client
    settings = get_settings()
    try:
        _client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=1500)
        _client.admin.command("ping")
        logger.info("Connected to MongoDB at %s (db=%s)", settings.mongodb_uri, settings.mongodb_database)
    except PyMongoError as exc:
        logger.warning(
            "MongoDB not reachable at %s (%s). The API will start, but data-backed "
            "endpoints will return DATABASE_UNAVAILABLE until Mongo is running and "
            "the app is restarted (or a request triggers reconnection).",
            settings.mongodb_uri,
            exc.__class__.__name__,
        )
        # Keep the client object around; pymongo lazily reconnects on next use.


def disconnect() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


def is_connected() -> bool:
    if _client is None:
        return False
    try:
        _client.admin.command("ping")
        return True
    except PyMongoError:
        return False


def get_database() -> Database:
    global _client
    settings = get_settings()
    if _client is None:
        connect()
    if _client is None or not is_connected():
        raise DatabaseUnavailableError(
            "MongoDB is not reachable. Install/start MongoDB and re-run "
            "'python scripts/seed_database.py' (see README)."
        )
    return _client[settings.mongodb_database]


# ---------------------------------------------------------------------------
# Indexes (spec section 26)
# ---------------------------------------------------------------------------
INDEX_SPECS = {
    "lots": ["lot_id", "equipment", "process_step", "process_start"],
    "wafers": ["wafer_id", "lot_id"],
    "equipment": ["equipment_id"],
    "process_runs": ["lot_id", "equipment_id", "timestamp", "process_step"],
    "defects": ["wafer_id", "defect_type"],
    "predictions": ["lot_id", "timestamp"],
    "root_causes": ["lot_id"],
    "recommendations": ["lot_id"],
    "model_versions": ["model_name", "version"],
    "maintenance": ["equipment_id", "timestamp"],
    "alerts": ["lot_id", "severity", "created_at"],
    "users": ["username"],
}


def ensure_indexes(db: Database) -> None:
    for collection_name, fields in INDEX_SPECS.items():
        collection = db[collection_name]
        for field in fields:
            collection.create_index([(field, ASCENDING)])
    logger.info("Ensured indexes on %d collections.", len(INDEX_SPECS))
