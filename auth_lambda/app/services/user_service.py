"""
User service - handles user-related database operations and business logic.
"""
from datetime import datetime, timezone
from typing import Optional
from fastapi import HTTPException
from boto3.dynamodb.conditions import Key

from app import config
from app.logger import get_logger
from app.utils.db_utils import (
    generate_user_id,
    normalize_phone_number,
)

logger = get_logger(__name__)


def get_user_by_email(email: str):
    """Get user by email using GSI"""
    try:
        normalized_email = email.lower().strip()
        response = config.users_table.query(
            IndexName="email-index",
            KeyConditionExpression=Key("email").eq(normalized_email)
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


def create_user_profile(user_data: dict, normalized_phone: str) -> dict:
    """
    Create a new user profile.
    
    Args:
        user_data: User data from request (name, email, etc.)
        normalized_phone: Normalized phone number
        
    Returns:
        Created user profile dict
    """
    email = user_data.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")
    
    user_id = generate_user_id()
    now_iso = datetime.now(timezone.utc).isoformat()

    user_item = {
        "user_id": user_id,
        "name": user_data.get("name", "").strip(),
        "phoneNumber": normalized_phone,
        "email": email.lower().strip(),  # Email is now mandatory
        "createdAt": now_iso,
    }

    try:
        config.users_table.put_item(Item=user_item)
        logger.info(f"[create_user_profile] Created user {user_id}")
        return user_item
    except Exception as e:
        logger.error(f"[create_user_profile] Error creating user: {e}")
        raise HTTPException(status_code=500, detail="Failed to create user profile")


def build_user_payload(user: dict) -> dict:
    """Build user payload for API responses"""
    if not user:
        return {}
    return {
        "user_id": user.get("user_id"),
        "name": user.get("name") or user.get("username"),
        "email": user.get("email"),
        "phoneNumber": user.get("phoneNumber"),
    }


def user_exists_by_phone(phone_number: str) -> bool:
    """
    Check if user exists by phone number.
    Used during signup to prevent duplicate registrations.
    
    Returns:
        True if user exists, False otherwise
    """
    user = get_user_by_phone(phone_number)
    return user is not None


def user_exists_by_email(email: str) -> bool:
    """
    Check if user exists by email.
    Used during signup to prevent duplicate registrations.
    
    Returns:
        True if user exists, False otherwise
    """
    user = get_user_by_email(email)
    return user is not None


def get_user_by_phone_for_admin(phone_number: str) -> Optional[dict]:
    """
    Get user by phone number for admin operations.
    Returns full user dict or None.
    """
    return get_user_by_phone(phone_number)

