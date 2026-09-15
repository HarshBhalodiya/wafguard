"""Shared FastAPI dependencies."""
from pymongo.database import Database

from app.database.mongodb import get_database


def get_db() -> Database:
    return get_database()
