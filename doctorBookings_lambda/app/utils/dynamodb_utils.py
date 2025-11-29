from app.config import AVAILABILITY_TABLE, BOOKING_TABLE
from boto3.dynamodb.conditions import Key
from datetime import datetime, time

def query_availability(doctor_id: str, date: str = None):
    """
    Query availability for a doctor.
    If date is provided, filters by date prefix in SK (e.g., "2025-11-28#")
    """
    expr = Key("PK").eq(doctor_id)
    if date:
        # SK format: date#slot_time, so we filter by date prefix
        expr &= Key("SK").begins_with(f"{date}#")
    return AVAILABILITY_TABLE.query(KeyConditionExpression=expr)

def query_booking(pk: str, starts_with: str = None):
    expr = Key("PK").eq(pk)
    if starts_with:
        expr &= Key("SK").begins_with(starts_with)
    return BOOKING_TABLE.query(KeyConditionExpression=expr)

def put_booking_item(item: dict):
    BOOKING_TABLE.put_item(Item=item)

def delete_booking(pk: str, sk: str):
    BOOKING_TABLE.delete_item(Key={"PK": pk, "SK": sk})

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

def update_availability(doctor_id: str, date: str, slot_time: str, available: bool, user_id: str = None, booking_id: str = None):
    """
    Update availability for a specific doctor, date, and slot time.
    PK: doctor_id
    SK: date#slot_time (e.g., "2025-11-28#10:00")
    """
    sk = f"{date}#{slot_time}"
    expiry_timestamp = get_expiry_timestamp(date)
    
    # Get existing item to preserve created_at if it exists
    existing = AVAILABILITY_TABLE.get_item(Key={"PK": doctor_id, "SK": sk})
    item = {
        "PK": doctor_id,
        "SK": sk,
        "available": available,
        "user_id": user_id,
        "booking_id": booking_id,
        "expiry_timestamp": expiry_timestamp
    }
    
    if "Item" in existing:
        item["created_at"] = existing["Item"].get("created_at", datetime.utcnow().isoformat())
    else:
        item["created_at"] = datetime.utcnow().isoformat()
    
    AVAILABILITY_TABLE.put_item(Item=item)
