from datetime import datetime, timedelta
from fastapi import HTTPException
from app.utils import dynamodb_utils
from app.config import BOOKING_TABLE
from app.logger import get_logger
import uuid

logger = get_logger(__name__)

def book_slot_logic(data):
    pk = f"{data.doctor_id}#{data.date}"
    sk = f"{data.start}#{data.user_id}"
    date_obj = datetime.strptime(data.date, "%Y-%m-%d")

    # Step 1: Fetch availability for that date
    res = dynamodb_utils.query_availability(data.doctor_id, data.date)

    # Check slot existence and availability
    availability_item = None
    expected_sk = f"{data.date}#{data.start}"
    for item in res.get("Items", []):
        if item["SK"] == expected_sk:
            # Check if slot is available (available=True and no user_id/booking_id)
            if item.get("available", True) and not item.get("user_id") and not item.get("booking_id"):
                availability_item = item
                break
    
    if not availability_item:
        raise HTTPException(400, "Slot unavailable or not found")

    # Step 2: Check existing booking
    existing = dynamodb_utils.query_booking(pk, starts_with=data.start)
    for item in existing.get("Items", []):
        if item["user_id"] == data.user_id:
            raise HTTPException(400, "User already booked this slot")

    # Step 3: Generate booking_id
    booking_id = str(uuid.uuid4())

    # Step 4: Save booking
    booking_item = {
        "PK": pk,
        "SK": sk,
        "doctor_id": data.doctor_id,
        "date": data.date,
        "start": data.start,
        "user_id": data.user_id
    }
    dynamodb_utils.put_booking_item(booking_item)

    # Step 5: Mark slot unavailable and associate with user_id and booking_id
    dynamodb_utils.update_availability(
        doctor_id=data.doctor_id,
        date=data.date,
        slot_time=data.start,
        available=False,
        user_id=data.user_id,
        booking_id=booking_id
    )

    return {"message": "Slot booked successfully."}


def cancel_slot_logic(data):
    pk = f"{data.doctor_id}#{data.date}"
    sk = f"{data.start}#{data.user_id}"

    # Step 1: Get booking to retrieve booking_id
    booking = BOOKING_TABLE.get_item(Key={"PK": pk, "SK": sk})
    if "Item" not in booking:
        raise HTTPException(400, "Booking not found")

    # Step 2: Delete booking
    dynamodb_utils.delete_booking(pk, sk)

    # Step 3: Mark slot available again and clear user_id and booking_id
    dynamodb_utils.update_availability(
        doctor_id=data.doctor_id,
        date=data.date,
        slot_time=data.start,
        available=True,
        user_id=None,
        booking_id=None
    )

    return {"message": "Booking cancelled and slot released."}
