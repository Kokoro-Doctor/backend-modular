"""
Doctor service - handles doctor-related database operations and business logic.
"""
from datetime import datetime, timezone, timedelta, time
from typing import Optional
from fastapi import HTTPException
from boto3.dynamodb.conditions import Key

from app import config
from app.logger import get_logger
from app.utils.db_utils import (
    generate_doctor_id,
    normalize_phone_number,
)

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
    # email = doctor_data.get("email")
    # if not email:
    #     raise HTTPException(status_code=400, detail="Email is required")
    
    doctor_id = generate_doctor_id()
    now_iso = datetime.now(timezone.utc).isoformat()

    doctor_item = {
        "doctor_id": doctor_id,
        # "doctorname": doctor_data.get("name", "").strip(),
        "phoneNumber": normalized_phone,
        # "email": email.lower().strip(),  # Email is now mandatory
        "createdAt": now_iso,
    }
    
    # Add optional fields if provided
    email = doctor_data.get("email")
    if email:
        doctor_item["email"] = email.lower().strip()
    
    name = doctor_data.get("name")
    if name:
        doctor_item["doctorname"] = name.strip()

    if doctor_data.get("specialization"):
        doctor_item["specialization"] = doctor_data.get("specialization")
    if doctor_data.get("experience") is not None:
        doctor_item["experience"] = doctor_data.get("experience")

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


def doctor_exists_by_phone(phone_number: str) -> bool:
    """
    Check if doctor exists by phone number.
    Used during signup to prevent duplicate registrations.
    
    Returns:
        True if doctor exists, False otherwise
    """
    doctor = get_doctor_by_phone(phone_number)
    return doctor is not None


def get_doctor_by_phone_for_admin(phone_number: str) -> Optional[dict]:
    """
    Get doctor by phone number for admin operations.
    Returns full doctor dict or None.
    """
    return get_doctor_by_phone(phone_number)


def get_expiry_timestamp(date_str: str) -> int:
    """
    Calculate expiry timestamp for end of day (23:59:59) in epoch time.
    
    Args:
        date_str: Date in YYYY-MM-DD format
        
    Returns:
        Epoch timestamp (seconds since Unix epoch) for end of day
    """
    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    end_of_day = datetime.combine(date_obj.date(), time(23, 59, 59))
    return int(end_of_day.timestamp())


def create_default_slots_for_doctor(doctor_id: str) -> None:
    """
    Create default availability slots for a doctor for the next 7 days.
    
    Default slots: 9:00 AM to 5:00 PM with 30-minute intervals
    (9:00, 9:30, 10:00, 10:30, 11:00, 11:30, 12:00, 12:30, 13:00, 13:30, 
     14:00, 14:30, 15:00, 15:30, 16:00, 16:30, 17:00)
    
    Args:
        doctor_id: The doctor's ID
    """
    try:
        # Generate dates for the next 7 days
        today = datetime.now(timezone.utc).date()
        dates = [today + timedelta(days=i) for i in range(7)]
        
        # Default time slots: 9:00 AM to 5:00 PM with 30-minute intervals
        # Format: HH:MM in 24-hour format
        default_slot_times = [
            "09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
            "12:00", "12:30", "13:00", "13:30", "14:00", "14:30",
            "15:00", "15:30", "16:00", "16:30", "17:00"
        ]
        
        created_at = datetime.utcnow().isoformat()
        
        # Create slots for each date
        for date_obj in dates:
            date_str = date_obj.strftime("%Y-%m-%d")
            expiry_timestamp = get_expiry_timestamp(date_str)
            
            for slot_time in default_slot_times:
                sk = f"{date_str}#{slot_time}"
                item = {
                    "PK": doctor_id,
                    "SK": sk,
                    "available": True,
                    "created_at": created_at,
                    "expiry_timestamp": expiry_timestamp
                }
                config.availability_table.put_item(Item=item)
        
        logger.info(f"[create_default_slots_for_doctor] Created default slots for doctor {doctor_id} for next 7 days")
    except Exception as e:
        logger.error(f"[create_default_slots_for_doctor] Error creating default slots for doctor {doctor_id}: {e}")
        # Don't raise exception - slot creation failure shouldn't prevent doctor signup
        # Log the error but allow signup to proceed

