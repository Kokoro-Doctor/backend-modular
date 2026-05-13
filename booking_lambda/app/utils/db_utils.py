"""
Pure utility functions for ID generation and phone number normalization.
Stateless helpers with no business logic or database access.
"""
import re
import uuid

from app.logger import get_logger

logger = get_logger(__name__)

SMS_COUNTRY_CODE = "+91"


def _prefixed_id(prefix: str) -> str:
    """Generate a prefixed UUID (e.g., USR_550e8400-e29b-41d4-a716-446655440000)"""
    return f"{prefix}_{str(uuid.uuid4())}"


def normalize_phone_number(phone: str) -> str:
    """
    Normalize phone number to E.164 format.
    Assumes Indian number (+91) for 10-digit numbers without prefix.
    """
    if not phone:
        return ""
    trimmed = str(phone).strip()
    if not trimmed:
        return ""
    digits_only = re.sub(r"\D", "", trimmed)
    if not digits_only:
        return ""
    if trimmed.startswith("+"):
        normalized = "+" + digits_only
        if len(normalized) < 8 or len(normalized) > 18:
            return ""
        if digits_only.startswith("91"):
            if len(digits_only) == 12:
                return normalized
            elif len(digits_only) == 11:
                return f"{SMS_COUNTRY_CODE}{digits_only[-10:]}"
            elif len(digits_only) == 10:
                return f"{SMS_COUNTRY_CODE}{digits_only}"
        return normalized
    if len(digits_only) == 10:
        return f"{SMS_COUNTRY_CODE}{digits_only}"
    if len(digits_only) == 12 and digits_only.startswith("91"):
        return f"+{digits_only}"
    if len(digits_only) == 11 and digits_only.startswith("91"):
        return f"{SMS_COUNTRY_CODE}{digits_only[-10:]}"
    if len(digits_only) < 10:
        return ""
    last_10 = digits_only[-10:]
    if len(last_10) == 10:
        return f"{SMS_COUNTRY_CODE}{last_10}"
    return ""


def generate_user_id() -> str:
    """Generate a new prefixed identifier for user_id"""
    return _prefixed_id("USR")
