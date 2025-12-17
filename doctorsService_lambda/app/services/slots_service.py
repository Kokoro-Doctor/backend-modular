"""
Slots service - handles doctor availability slot management.
"""
from datetime import datetime, time
from fastapi import HTTPException
from app.config import DOCTORS_TABLE, AVAILABILITY_TABLE
from app.logger import get_logger

logger = get_logger(__name__)


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


def set_availability(doctor_id: str, date: str, slots: list) -> dict:
    """Set availability slots for a doctor on a specific date"""
    doctor = DOCTORS_TABLE.get_item(Key={"doctor_id": doctor_id})
    if "Item" not in doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    # Validate date format
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    pk = doctor_id
    created_at = datetime.utcnow().isoformat()
    expiry_timestamp = get_expiry_timestamp(date)
    
    for slot in slots:
        sk = f"{date}#{slot.start}"
        item = {
            "PK": pk,
            "SK": sk,
            "available": True,
            "created_at": created_at,
            "expiry_timestamp": expiry_timestamp
        }
        AVAILABILITY_TABLE.put_item(Item=item)

    return {"message": f"Availability for {date} set successfully."}


def update_slot(doctor_id: str, date: str, slot_time: str, available: bool) -> dict:
    """Update a specific slot's availability"""
    # Validate date format
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    pk = doctor_id
    sk = f"{date}#{slot_time}"
    expiry_timestamp = get_expiry_timestamp(date)
    
    # Get existing item to preserve user_id and booking_id if they exist
    existing = AVAILABILITY_TABLE.get_item(Key={"PK": pk, "SK": sk})
    item = {
        "PK": pk,
        "SK": sk,
        "available": available,
        "expiry_timestamp": expiry_timestamp
    }
    
    if "Item" in existing:
        existing_item = existing["Item"]
        if existing_item.get("user_id"):
            item["user_id"] = existing_item["user_id"]
        if existing_item.get("booking_id"):
            item["booking_id"] = existing_item["booking_id"]
        item["created_at"] = existing_item.get("created_at", datetime.utcnow().isoformat())
    else:
        item["created_at"] = datetime.utcnow().isoformat()
    
    AVAILABILITY_TABLE.put_item(Item=item)
    return {"message": f"Slot {'enabled' if available else 'disabled'} successfully"}

