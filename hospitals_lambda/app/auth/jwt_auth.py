"""
JWT utilities for hospital authentication.
"""
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

import jwt
from fastapi import Header, HTTPException

from app.config import JWT_EXPIRE_HOURS, JWT_SECRET
from app.logger import get_logger

logger = get_logger(__name__)


def create_hospital_token(hospital_id: str) -> str:
    """Mint a signed HS256 JWT for a hospital session."""
    if not JWT_SECRET:
        logger.error("[jwt_auth] JWT_SECRET is not configured")
        raise HTTPException(status_code=503, detail="JWT secret not configured")

    payload = {
        "sub": hospital_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _bearer_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid Authorization header (expected: Bearer <token>)",
        )
    return authorization[7:].strip()


def get_current_hospital(
    authorization: Annotated[Optional[str], Header(include_in_schema=False)] = None,
) -> str:
    """
    Validates the JWT from the Authorization: Bearer header and returns hospital_id.
    Uses Header() instead of HTTPBearer() so OpenAPI/Swagger does not show Authorize UI.
    """
    token = _bearer_token(authorization)
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        hospital_id: str = payload.get("sub", "")
        if not hospital_id:
            raise ValueError("Missing sub claim")
        logger.info(f"[jwt_auth] Token valid for hospital_id={hospital_id!r}")
        return hospital_id
    except jwt.ExpiredSignatureError:
        logger.warning("[jwt_auth] Token has expired")
        raise HTTPException(status_code=401, detail="Session expired, please log in again")
    except jwt.InvalidTokenError as e:
        logger.warning(f"[jwt_auth] Invalid token: {e}")
        raise HTTPException(status_code=401, detail="Invalid token")


def assert_hospital_id_matches_token(body_hospital_id: str, token_hospital_id: str) -> None:
    """Require the request body's hospital_id to match the JWT `sub` claim."""
    if (body_hospital_id or "").strip() != (token_hospital_id or "").strip():
        logger.warning(
            f"[jwt_auth] Body hospital_id mismatch token_hospital_id={token_hospital_id!r} "
            f"body_hospital_id={body_hospital_id!r}"
        )
        raise HTTPException(
            status_code=403,
            detail="hospital_id in request does not match authenticated hospital",
        )
