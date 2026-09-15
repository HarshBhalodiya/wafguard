from fastapi import APIRouter, Depends
from pymongo.database import Database

from app.api.deps import get_db
from app.database.repositories.user_repository import UserRepository
from app.schemas.auth import LoginRequest, TokenResponse
from app.utils.exceptions import UnauthorizedError
from app.utils.responses import success
from app.utils.security import create_access_token, verify_password

router = APIRouter()


@router.post("/auth/login")
def login(payload: LoginRequest, db: Database = Depends(get_db)):
    user = UserRepository(db).get_by_username(payload.username)
    if not user or not verify_password(payload.password, user["hashed_password"]):
        raise UnauthorizedError("Invalid username or password.")

    token = create_access_token(subject=user["username"], role=user["role"])
    return success(
        TokenResponse(
            access_token=token,
            role=user["role"],
            full_name=user["full_name"],
            username=user["username"],
        ).model_dump()
    )
