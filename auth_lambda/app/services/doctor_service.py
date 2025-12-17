"""
Doctor service - handles doctor-related database operations and business logic.
"""
from datetime import datetime, timezone
from typing import Optional
from fastapi import HTTPException
from boto3.dynamodb.conditions import Key

from app import config
from app.logger import get_logger
from app.utils.db_utils import (
    generate_doctor_id,
    normalize_phone_number,
)
from boto3.dynamodb.conditions import Key

logger = get_logger(__name__)


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


def create_doctor_profile(doctor_data: dict, normalized_phone: str) -> dict:
    """
    Create a new doctor profile.
    
    Args:
        doctor_data: Doctor data from request (name, email, specialization, etc.)
        normalized_phone: Normalized phone number
        
    Returns:
        Created doctor profile dict
    """
    doctor_id = generate_doctor_id()
    now_iso = datetime.now(timezone.utc).isoformat()

    doctor_item = {
        "doctor_id": doctor_id,
        "doctorname": doctor_data.get("name", "").strip(),
        "phoneNumber": normalized_phone,
        "createdAt": now_iso,
    }

    if doctor_data.get("specialization"):
        doctor_item["specialization"] = doctor_data.get("specialization")
    if doctor_data.get("experience") is not None:
        doctor_item["experience"] = doctor_data.get("experience")
    if doctor_data.get("email"):
        doctor_item["email"] = doctor_data.get("email").lower()

    try:
        config.doctors_table.put_item(Item=doctor_item)
        logger.info(f"[create_doctor_profile] Created doctor {doctor_id}")
        return doctor_item
    except Exception as e:
        logger.error(f"[create_doctor_profile] Error creating doctor: {e}")
        raise HTTPException(status_code=500, detail="Failed to create doctor profile")


def build_doctor_payload(doctor: dict) -> dict:
    """Build doctor payload for API responses"""
    if not doctor:
        return {}
    return {
        "doctor_id": doctor.get("doctor_id"),
        "name": doctor.get("name") or doctor.get("doctorname"),
        "email": doctor.get("email"),
        "phoneNumber": doctor.get("phoneNumber"),
        "specialization": doctor.get("specialization"),
        "experience": doctor.get("experience"),
    }

