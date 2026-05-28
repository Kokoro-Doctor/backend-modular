from typing import Optional
import jwt
from fastapi import HTTPException

from app import config
from app.logger import get_logger

logger = get_logger(__name__)


def verify_jwt(token: str) -> dict:
    """Verify a Kokoro JWT and return the decoded payload."""
    try:
        payload = jwt.decode(
            token,
            config.JWT_SECRET,
            algorithms=[config.JWT_ALGORITHM],
            options={"verify_iss": False},
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


def get_user_id_from_token(authorization: Optional[str]) -> str:
    """Extract user_id from Bearer token in Authorization header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization header missing or malformed")
    token = authorization.removeprefix("Bearer ").strip()
    payload = verify_jwt(token)
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token does not contain user_id")
    return user_id
