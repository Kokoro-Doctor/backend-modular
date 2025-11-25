from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from app import config
from app.logger import get_logger

logger = get_logger(__name__)


def _default_expiry(minutes: Optional[int] = None) -> int:
    ttl_minutes = minutes or config.JWT_EXP_MINUTES
    exp_dt = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    return int(exp_dt.timestamp())


def create_jwt(
    *,
    phone_number: str,
    role: str,
    user_id: Optional[str] = None,
    doctor_id: Optional[str] = None,
    expires_minutes: Optional[int] = None
) -> str:
    """Create a signed JWT for a verified session."""
    if role not in ("user", "doctor"):
        raise ValueError("Unsupported role for JWT token")

    subject = user_id or doctor_id or phone_number
    payload = {
        "sub": subject,
        "iss": config.JWT_ISSUER,
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": _default_expiry(expires_minutes),
        "role": role,
        "phoneNumber": phone_number,
    }

    if user_id:
        payload["user_id"] = user_id
    if doctor_id:
        payload["doctor_id"] = doctor_id

    token = jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)
    logger.debug("[JWT] Created token for %s (%s)", phone_number, role)
    return token

