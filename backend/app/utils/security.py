"""
Minimal JWT authentication (spec section 40).

Kept intentionally simple: seeded users in the `users` collection, JWT
bearer tokens, role-based dependency helpers. Not meant to be a hardened
enterprise auth system — just enough that the local MVP has real login
and role gating instead of an open API.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, Header
from jose import JWTError, jwt

from app.config import get_settings
from app.utils.exceptions import ForbiddenError, UnauthorizedError

ROLES = ["admin", "process_engineer", "yield_engineer", "equipment_engineer", "viewer"]

# Using the `bcrypt` package directly rather than passlib's CryptContext:
# passlib's bcrypt backend probes `bcrypt.__about__.__version__` to detect
# the installed version, which recent bcrypt releases (4.1+) removed —
# that breaks passlib's self-test with a misleading "password too long"
# error. Hashing/verifying directly sidesteps that fragile version sniff.
_BCRYPT_MAX_BYTES = 72  # bcrypt's own hard limit


def hash_password(plain_password: str) -> str:
    truncated = plain_password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(truncated, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    truncated = plain_password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.checkpw(truncated, hashed_password.encode("utf-8"))


def create_access_token(subject: str, role: str) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": subject, "role": role, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise UnauthorizedError("Invalid or expired token.") from exc


async def get_current_user(authorization: Optional[str] = Header(default=None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("Missing bearer token.")
    token = authorization.split(" ", 1)[1]
    payload = decode_access_token(token)
    return {"username": payload.get("sub"), "role": payload.get("role")}


def require_roles(*allowed_roles: str):
    async def _dependency(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in allowed_roles:
            raise ForbiddenError(f"Role '{user['role']}' is not permitted to perform this action.")
        return user

    return _dependency
