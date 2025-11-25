"""
Database utility functions for identity and lookup helpers.
"""
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from boto3.dynamodb.conditions import Key

from app import config
from app.logger import get_logger

logger = get_logger(__name__)


def _prefixed_id(prefix: str) -> str:
    """Generate a prefixed UUID (e.g., USR#550e8400-e29b-41d4-a716-446655440000)"""
    return f"{prefix}#{str(uuid.uuid4())}"


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

def get_user_by_email(email: str):
    """Get user by email using GSI"""
    try:
        response = config.users_table.query(
            IndexName="email-index",
            KeyConditionExpression=Key("email").eq(email)
        )
        items = response.get("Items", [])
        return items[0] if items else None
    except Exception as e:
        logger.error(f"[get_user_by_email] Error querying user by email {email}: {e}")
        return None

def get_user_by_phone(phoneNumber: str):
    """Get user by phone number using GSI. Phone number is normalized before lookup."""
    try:
        normalized_phone = normalize_phone_number(phoneNumber)
        response = config.users_table.query(
            IndexName="phone-index",
            KeyConditionExpression=Key("phoneNumber").eq(normalized_phone)
        )
        items = response.get("Items", [])
        return items[0] if items else None
    except Exception as e:
        logger.error(f"[get_user_by_phone] Error querying user by phone {phoneNumber}: {e}")
        return None

def get_user_by_id(user_id: str):
    """Get user by user_id (primary key)"""
    try:
        response = config.users_table.get_item(Key={"user_id": user_id})
        return response.get("Item")
    except Exception as e:
        logger.error(f"[get_user_by_id] Error getting user by id {user_id}: {e}")
        return None

def get_doctor_by_email(email: str):
    """Get doctor by email using GSI"""
    try:
        response = config.doctors_table.query(
            IndexName="email-index",
            KeyConditionExpression=Key("email").eq(email)
        )
        items = response.get("Items", [])
        return items[0] if items else None
    except Exception as e:
        logger.error(f"[get_doctor_by_email] Error querying doctor by email {email}: {e}")
        return None

def get_doctor_by_phone(phoneNumber: str):
    """Get doctor by phone number using GSI. Phone number is normalized before lookup."""
    try:
        normalized_phone = normalize_phone_number(phoneNumber)
        response = config.doctors_table.query(
            IndexName="phone-index",
            KeyConditionExpression=Key("phoneNumber").eq(normalized_phone)
        )
        items = response.get("Items", [])
        return items[0] if items else None
    except Exception as e:
        logger.error(f"[get_doctor_by_phone] Error querying doctor by phone {phoneNumber}: {e}")
        return None

def get_doctor_by_id(doctor_id: str):
    """Get doctor by doctor_id (primary key)"""
    try:
        response = config.doctors_table.get_item(Key={"doctor_id": doctor_id})
        return response.get("Item")
    except Exception as e:
        logger.error(f"[get_doctor_by_id] Error getting doctor by id {doctor_id}: {e}")
        return None

def get_auth_token_by_email(email: str, purpose: str):
    """Get auth token by email and purpose using GSI"""
    try:
        response = config.auth_tokens_table.query(
            IndexName="email-index",
            KeyConditionExpression=Key("email").eq(email) & Key("purpose").eq(purpose)
        )
        items = response.get("Items", [])
        return items[0] if items else None
    except Exception as e:
        logger.error(f"[get_auth_token_by_email] Error querying token by email {email}: {e}")
        return None

def get_auth_token_by_phone(phoneNumber: str, purpose: str):
    """Get auth token by phone and purpose using GSI. Returns the most recent non-expired token. Phone number is normalized before lookup."""
    try:
        normalized_phone = normalize_phone_number(phoneNumber)
        response = config.auth_tokens_table.query(
            IndexName="phone-index",
            KeyConditionExpression=Key("phoneNumber").eq(normalized_phone) & Key("purpose").eq(purpose)
        )
        items = response.get("Items", [])
        if not items:
            return None
        
        # Sort by createdAt descending to get the most recent OTP first
        items.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        
        # Return the most recent non-expired OTP
        current_timestamp = int(datetime.now(timezone.utc).timestamp())
        for item in items:
            ttl = item.get("ttl")
            if not ttl or ttl >= current_timestamp:
                return item
        
        # If all are expired, return the most recent one anyway (for proper error handling)
        return items[0]
    except Exception as e:
        logger.error(f"[get_auth_token_by_phone] Error querying token by phone {phoneNumber}: {e}")
        return None


def get_auth_record(phoneNumber: str) -> Optional[Dict[str, Any]]:
    """Fetch auth metadata for a normalized phone number."""
    normalized = normalize_phone_number(phoneNumber)
    if not normalized:
        return None
    try:
        response = config.auth_table.get_item(Key={"phoneNumber": normalized})
        return response.get("Item")
    except Exception as e:
        logger.error(f"[get_auth_record] Error fetching auth record for {phoneNumber}: {e}")
        return None


def put_auth_record(item: Dict[str, Any]) -> None:
    """Persist a complete auth record."""
    try:
        config.auth_table.put_item(Item=item)
    except Exception as e:
        logger.error(f"[put_auth_record] Failed to write auth record: {e}")
        raise


def update_auth_record(phoneNumber: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update specific fields on an auth record."""
    normalized = normalize_phone_number(phoneNumber)
    if not normalized:
        raise ValueError("Invalid phone number for auth record update")

    expressions = []
    attr_values: Dict[str, Any] = {}
    attr_names: Dict[str, str] = {}
    for idx, (field, value) in enumerate(updates.items()):
        name_placeholder = f"#f{idx}"
        value_placeholder = f":v{idx}"
        expressions.append(f"{name_placeholder} = {value_placeholder}")
        attr_names[name_placeholder] = field
        attr_values[value_placeholder] = value

    update_expression = "SET " + ", ".join(expressions)

    try:
        response = config.auth_table.update_item(
            Key={"phoneNumber": normalized},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=attr_names,
            ExpressionAttributeValues=attr_values,
            ReturnValues="ALL_NEW"
        )
        return response.get("Attributes", {})
    except Exception as e:
        logger.error(f"[update_auth_record] Failed to update auth record for {phoneNumber}: {e}")
        raise


def ensure_auth_record(phoneNumber: str) -> Dict[str, Any]:
    """Retrieve an auth record, creating a new shell if needed."""
    normalized = normalize_phone_number(phoneNumber)
    if not normalized:
        raise ValueError("Invalid phone number for auth record creation")

    existing = get_auth_record(normalized)
    if existing:
        if "has_password" not in existing:
            try:
                updated = update_auth_record(normalized, {"has_password": False})
                if updated:
                    return updated
            except Exception:
                logger.warning("[ensure_auth_record] Failed to backfill has_password for %s", normalized)
        return existing

    now = datetime.now(timezone.utc).isoformat()
    record = {
        "phoneNumber": normalized,
        "role": None,
        "is_verified": False,
        "user_id": None,
        "doctor_id": None,
        "has_password": False,
        "created_at": now,
        "updated_at": now,
        "last_login": None,
        "last_verified_at": None
    }
    put_auth_record(record)
    return record

def find_user_by_email_or_phone(email: str = None, phoneNumber: str = None):
    """Find user by email or phone number. Phone numbers are normalized before lookup."""
    if email:
        user = get_user_by_email(email)
        if user:
            return user
    if phoneNumber:
        # Normalize phone number before lookup
        normalized_phone = normalize_phone_number(phoneNumber)
        user = get_user_by_phone(normalized_phone)
        if user:
            return user
    return None

def find_doctor_by_email_or_phone(email: str = None, phoneNumber: str = None):
    """Find doctor by email or phone number. Phone numbers are normalized before lookup."""
    if email:
        doctor = get_doctor_by_email(email)
        if doctor:
            return doctor
    if phoneNumber:
        # Normalize phone number before lookup
        normalized_phone = normalize_phone_number(phoneNumber)
        doctor = get_doctor_by_phone(normalized_phone)
        if doctor:
            return doctor
    return None

