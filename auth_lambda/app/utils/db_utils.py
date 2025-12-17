"""
Pure utility functions for ID generation and phone number normalization.
These are stateless helper functions with no business logic or database access.
"""
import re
import uuid

from app import config
from app.logger import get_logger

logger = get_logger(__name__)


def _prefixed_id(prefix: str) -> str:
    """Generate a prefixed UUID (e.g., USR_550e8400-e29b-41d4-a716-446655440000)"""
    return f"{prefix}_{str(uuid.uuid4())}"


def normalize_phone_number(phone: str) -> str:
    """
    Normalize phone number to E.164 format with country code.
    Handles various input formats:
    - 10-digit numbers: adds +91 prefix
    - 12-digit numbers starting with 91: adds + prefix
    - Numbers already with +: returns as-is
    - Other formats: attempts to extract last 10 digits and add +91
    
    Args:
        phone: Raw phone number string
        
    Returns:
        Normalized phone number in E.164 format (e.g., +919587733170)
    """
    if not phone:
        return ""
    
    trimmed = phone.strip()
    if not trimmed:
        return ""
    
    # If it already starts with +, return as-is (assuming it's already formatted)
    if trimmed.startswith("+"):
        return trimmed
    
    # Remove all non-digit characters
    digits_only = re.sub(r"\D", "", trimmed)
    if not digits_only:
        return trimmed  # Return original if no digits found
    
    # If it's exactly 10 digits, assume it's an Indian number and add +91
    if len(digits_only) == 10:
        return f"{config.SMS_COUNTRY_CODE}{digits_only}"
    
    # If it's 12 digits and starts with 91, add +
    if len(digits_only) == 12 and digits_only.startswith("91"):
        return f"+{digits_only}"
    
    # Otherwise, try to extract last 10 digits and add +91
    last_10_digits = digits_only[-10:]
    if len(last_10_digits) == 10:
        return f"{config.SMS_COUNTRY_CODE}{last_10_digits}"
    
    # If we can't normalize, return the original trimmed value
    logger.warning(f"[normalize_phone_number] Could not normalize phone number: {phone}")
    return trimmed


def generate_user_id() -> str:
    """Generate a new prefixed identifier for user_id"""
    return _prefixed_id("USR")


def generate_doctor_id() -> str:
    """Generate a new prefixed identifier for doctor_id"""
    return _prefixed_id("DOC")


def generate_token_id() -> str:
    """Generate a new UUID for token_id"""
    return str(uuid.uuid4())
