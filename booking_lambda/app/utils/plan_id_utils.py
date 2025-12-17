"""
Plan ID Utility - generates and validates human-readable plan IDs

IMPORTANT: plan_id is ONLY an identifier. The database table is ALWAYS the source of truth.
Never derive price, validity, or any plan attributes from the plan_id itself.
The plan_id format is for human readability only.
"""
import re
from typing import Optional, Tuple
from decimal import Decimal


# Plan ID format: PLAN_<PRICE>_<DURATION>D_<SCOPE>
# Examples:
#   PLAN_999_30D_ALL          - ₹999, 30 days, all doctors
#   PLAN_1999_30D_DOC123       - ₹1999, 30 days, doctor-specific
#   PLAN_4999_90D_ALL          - ₹4999, 90 days, all doctors
PLAN_ID_PATTERN = re.compile(r'^PLAN_(\d+)_(\d+)D_(.+)$')


def generate_plan_id(price: Decimal, validity_days: int, doctor_id: str) -> str:
    """
    Generate a human-readable plan_id from plan attributes.
    
    Format: PLAN_<PRICE>_<DURATION>D_<SCOPE>
    
    Args:
        price: Plan price (will be converted to integer)
        validity_days: Validity period in days
        doctor_id: Doctor ID or 'ALL' for global plans
    
    Returns:
        Human-readable plan_id string
    
    Example:
        generate_plan_id(Decimal('999.00'), 30, 'ALL')
        -> 'PLAN_999_30D_ALL'
    """
    # Convert price to integer (remove decimals)
    price_int = int(float(price))
    
    # Validate inputs
    if price_int <= 0:
        raise ValueError("Price must be greater than 0")
    if validity_days <= 0:
        raise ValueError("Validity days must be greater than 0")
    if not doctor_id or not doctor_id.strip():
        raise ValueError("Doctor ID cannot be empty")
    
    # Sanitize doctor_id for use in plan_id (remove special chars that might break format)
    scope = doctor_id.strip().upper()
    # Replace any non-alphanumeric chars (except underscore) with underscore
    scope = re.sub(r'[^A-Z0-9_]', '_', scope)
    
    return f"PLAN_{price_int}_{validity_days}D_{scope}"


def parse_plan_id(plan_id: str) -> Optional[Tuple[int, int, str]]:
    """
    Parse a plan_id to extract its components (for validation/logging only).
    
    IMPORTANT: This is for validation/logging purposes ONLY.
    Never use parsed values to derive plan attributes - always fetch from database.
    
    Args:
        plan_id: Plan ID string to parse
    
    Returns:
        Tuple of (price, validity_days, doctor_id) if valid, None otherwise
    
    Example:
        parse_plan_id('PLAN_999_30D_ALL')
        -> (999, 30, 'ALL')
    """
    match = PLAN_ID_PATTERN.match(plan_id)
    if not match:
        return None
    
    try:
        price = int(match.group(1))
        validity_days = int(match.group(2))
        scope = match.group(3)
        return (price, validity_days, scope)
    except (ValueError, IndexError):
        return None


def validate_plan_id_format(plan_id: str) -> bool:
    """
    Validate that a plan_id matches the expected format.
    
    This is for input validation only. The database is still the source of truth.
    
    Args:
        plan_id: Plan ID string to validate
    
    Returns:
        True if format is valid, False otherwise
    """
    return parse_plan_id(plan_id) is not None


def validate_plan_id_matches_data(plan_id: str, price: Decimal, validity_days: int, doctor_id: str) -> bool:
    """
    Validate that a plan_id matches the provided plan data.
    
    This is a consistency check - useful when creating/updating plans.
    The database is still the source of truth for all operations.
    
    Args:
        plan_id: Plan ID to validate
        price: Plan price from database
        validity_days: Validity days from database
        doctor_id: Doctor ID from database
    
    Returns:
        True if plan_id matches data, False otherwise
    """
    parsed = parse_plan_id(plan_id)
    if not parsed:
        return False
    
    parsed_price, parsed_validity, parsed_scope = parsed
    
    # Compare values (allow small floating point differences for price)
    price_int = int(float(price))
    scope_normalized = doctor_id.strip().upper()
    scope_normalized = re.sub(r'[^A-Z0-9_]', '_', scope_normalized)
    
    return (
        parsed_price == price_int and
        parsed_validity == validity_days and
        parsed_scope == scope_normalized
    )

