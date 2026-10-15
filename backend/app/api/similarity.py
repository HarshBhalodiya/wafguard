from typing import Optional

from fastapi import APIRouter, Depends, Query
from pymongo.database import Database

from app.api.deps import get_db
from app.services import similarity_service
from app.utils.responses import success

router = APIRouter()


@router.get("/similar/{lot_id}")
def get_similar_lots(lot_id: str, k: int = Query(default=5, ge=1, le=20), db: Database = Depends(get_db)):
    """Original parameter-space similarity search (kept stable for existing clients)."""
    return success(similarity_service.find_similar_lots(db, lot_id, k=k))


@router.get("/similarity/{lot_id}")
def get_similar_multi(
    lot_id: str,
    k: int = Query(default=5, ge=1, le=20),
    spaces: Optional[str] = Query(
        default=None,
        description="Comma-separated subset of: parameter,temporal,vision (default: all)",
    ),
    db: Database = Depends(get_db),
):
    """Multi-space similarity (Phase 3, P3-5): parameter kNN + cosine search over the
    temporal and wafer-map embeddings, each match tagged with the space it came from."""
    requested = tuple(s.strip() for s in spaces.split(",")) if spaces else similarity_service.EMBEDDING_SPACES
    return success(similarity_service.find_similar_multi(db, lot_id, k=k, spaces=requested))
