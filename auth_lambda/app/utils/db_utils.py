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
    Normalize phone number to E.164 format (international standard: +[country code][number]).
    
    Handles international phone numbers:
    - Numbers with + prefix: preserves country code and validates format
    - Numbers without + prefix: assumes Indian number (+91) for backward compatibility
    - Supports all international country codes (not just +91)
    
    Examples:
    - +14155551234 (US) -> +14155551234 (preserved)
    - +918533053387 (India) -> +918533053387 (preserved)
    - 8533053387 (India, no prefix) -> +918533053387 (adds +91)
    - 918533053387 (India, no +) -> +918533053387 (adds +)
    
    Args:
        phone: Raw phone number string
        
    Returns:
        Normalized phone number in E.164 format (e.g., +918533053387, +14155551234)
        Returns empty string if phone number cannot be normalized
    """
    if not phone:
        return ""
    
    trimmed = phone.strip()
    if not trimmed:
        return ""
    
    # Remove all non-digit characters to get digits only
    digits_only = re.sub(r"\D", "", trimmed)
    if not digits_only:
        logger.warning(f"[normalize_phone_number] No digits found in phone number: {phone}")
        return ""
    
    # Handle numbers that already have + prefix (international format)
    if trimmed.startswith("+"):
        # If it already has + prefix, preserve the country code
        # Just ensure it's properly formatted (remove any spaces, ensure + is present)
        normalized = "+" + digits_only
        
        # Basic validation: E.164 format requires at least 7 digits after country code
        # Minimum total length: +1 (country code) + 7 (number) = 8 characters
        # Maximum reasonable length: +3 (country code) + 15 (max number) = 18 characters
        if len(normalized) < 8:
            logger.warning(f"[normalize_phone_number] Phone number too short: {phone} (normalized: {normalized})")
            return ""
        if len(normalized) > 18:
            logger.warning(f"[normalize_phone_number] Phone number too long: {phone} (normalized: {normalized})")
            return ""
        
        # Special handling for Indian numbers (+91) to fix common issues
        if digits_only.startswith("91"):
            # If it's 12 digits starting with 91, it's correct: +91 + 10 digits
            if len(digits_only) == 12:
                return normalized
            # If it's 11 digits starting with 91, extract last 10 digits
            elif len(digits_only) == 11:
                logger.warning(f"[normalize_phone_number] Invalid length for +91 number: {phone}, extracting last 10 digits")
                return f"+91{digits_only[-10:]}"
            # If it's 10 digits, add +91 (shouldn't happen with + prefix, but handle it)
            elif len(digits_only) == 10:
                logger.warning(f"[normalize_phone_number] 10-digit number with + prefix (assuming Indian): {phone}")
                return f"+91{digits_only}"
        
        # For other country codes, return as-is (already in E.164 format)
        return normalized
    
    # Handle numbers without + prefix
    # Default to Indian number (+91) for backward compatibility
    
    # If it's exactly 10 digits, assume Indian number and add +91
    if len(digits_only) == 10:
        # Check if it starts with 91 (invalid for Indian numbers - likely user error)
        if digits_only.startswith("91"):
            logger.warning(f"[normalize_phone_number] 10-digit number starts with 91 (likely error): {phone}. Normalizing to +91{digits_only} (13 digits - may need manual review)")
        return f"{config.SMS_COUNTRY_CODE}{digits_only}"
    
    # If it's 12 digits and starts with 91, add + prefix (Indian number without +)
    if len(digits_only) == 12 and digits_only.startswith("91"):
        return f"+{digits_only}"
    
    # If it's 11 digits starting with 91, extract last 10 digits and add +91
    if len(digits_only) == 11 and digits_only.startswith("91"):
        logger.warning(f"[normalize_phone_number] 11-digit number starting with 91: {phone}, extracting last 10 digits")
        return f"{config.SMS_COUNTRY_CODE}{digits_only[-10:]}"
    
    # If it's more than 12 digits, try to detect if it's an Indian number
    if len(digits_only) > 12:
        # Check if last 12 digits start with 91 (Indian number)
        last_12 = digits_only[-12:]
        if last_12.startswith("91"):
            return f"+{last_12}"
        else:
            # Could be international number without +, but we can't determine country code
            # Default to treating last 10 digits as Indian number
            logger.warning(f"[normalize_phone_number] Long number without + prefix: {phone}, treating last 10 digits as Indian")
            return f"{config.SMS_COUNTRY_CODE}{digits_only[-10:]}"
    
    # If it's less than 10 digits, can't normalize (too short)
    if len(digits_only) < 10:
        logger.warning(f"[normalize_phone_number] Phone number too short: {phone} (only {len(digits_only)} digits)")
        return ""
    
    # Fallback: extract last 10 digits and add +91 (assume Indian)
    last_10_digits = digits_only[-10:]
    if len(last_10_digits) == 10:
        logger.warning(f"[normalize_phone_number] Using fallback normalization (assuming Indian): {phone}")
        return f"{config.SMS_COUNTRY_CODE}{last_10_digits}"
    
    # If we can't normalize, log warning and return empty string
    logger.warning(f"[normalize_phone_number] Could not normalize phone number: {phone}")
    return ""


def generate_user_id() -> str:
    """Generate a new prefixed identifier for user_id"""
    return _prefixed_id("usr")


def generate_doctor_id() -> str:
    """Generate a new prefixed identifier for doctor_id"""
    return _prefixed_id("dr")


def generate_token_id() -> str:
    """Generate a new UUID for token_id"""
    return str(uuid.uuid4())
