"""
Slots service - handles doctor availability slot management.
"""
from datetime import datetime, time, timedelta, timezone
from fastapi import HTTPException
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key
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


def set_availability(doctor_id: str, days: int, slots: list) -> dict:
    """
    Set availability slots for a doctor for the next X days starting from today.
    
    Args:
        doctor_id: Doctor identifier
        days: Number of days to create slots for (starting from today)
        slots: List of slot objects with start and end times
        
    Returns:
        Success message with number of days and slots created
    """
    doctor = DOCTORS_TABLE.get_item(Key={"doctor_id": doctor_id})
    if "Item" not in doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    # Validate days parameter
    if days < 1:
        raise HTTPException(status_code=400, detail="Days must be at least 1")
    if days > 90:
        raise HTTPException(status_code=400, detail="Days cannot exceed 90")

    # Generate dates for the next X days starting from today
    today = datetime.now(timezone.utc).date()
    dates = [today + timedelta(days=i) for i in range(days)]
    
    pk = doctor_id
    created_at = datetime.utcnow().isoformat()
    
    # Extract slot times from slots list
    slot_times = [slot.start for slot in slots]
    
    total_slots_created = 0
    
    # Create slots for each date
    for date_obj in dates:
        date_str = date_obj.strftime("%Y-%m-%d")
        expiry_timestamp = get_expiry_timestamp(date_str)
        
        for slot_time in slot_times:
            sk = f"{date_str}#{slot_time}"
            item = {
                "PK": pk,
                "SK": sk,
                "available": True,
                "created_at": created_at,
                "expiry_timestamp": expiry_timestamp
            }
            AVAILABILITY_TABLE.put_item(Item=item)
            total_slots_created += 1

    logger.info(f"[set_availability] Created {total_slots_created} slots for doctor {doctor_id} across {days} days")
    return {
        "message": f"Availability set successfully for {days} days.",
        "days": days,
        "slots_per_day": len(slot_times),
        "total_slots_created": total_slots_created
    }


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


def clear_all_slots(doctor_id: str) -> dict:
    """
    Clear all availability slots for a doctor.
    
    Args:
        doctor_id: Doctor identifier
        
    Returns:
        Success message with number of slots deleted
    """
    # Verify doctor exists
    doctor = DOCTORS_TABLE.get_item(Key={"doctor_id": doctor_id})
    if "Item" not in doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    pk = doctor_id
    total_deleted = 0
    
    try:
        # Query all items with this partition key (doctor_id)
        # DynamoDB query returns items with the same partition key
        response = AVAILABILITY_TABLE.query(
            KeyConditionExpression=Key("PK").eq(pk)
        )
        
        items = response.get("Items", [])
        
        # Handle pagination - DynamoDB query can return paginated results
        while "LastEvaluatedKey" in response:
            response = AVAILABILITY_TABLE.query(
                KeyConditionExpression=Key("PK").eq(pk),
                ExclusiveStartKey=response["LastEvaluatedKey"]
            )
            items.extend(response.get("Items", []))
        
        # Delete items in batches (DynamoDB batch_write_item can handle up to 25 items per request)
        batch_size = 25
        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            with AVAILABILITY_TABLE.batch_writer() as batch_writer:
                for item in batch:
                    batch_writer.delete_item(
                        Key={
                            "PK": item["PK"],
                            "SK": item["SK"]
                        }
                    )
                    total_deleted += 1
        
        logger.info(f"[clear_all_slots] Deleted {total_deleted} slots for doctor {doctor_id}")
        return {
            "message": f"All slots cleared successfully for doctor {doctor_id}.",
            "doctor_id": doctor_id,
            "slots_deleted": total_deleted
        }
        
    except ClientError as e:
        logger.error(f"[clear_all_slots] DynamoDB error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to clear slots: {str(e)}")
    except Exception as e:
        logger.error(f"[clear_all_slots] Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to clear slots: {str(e)}")

