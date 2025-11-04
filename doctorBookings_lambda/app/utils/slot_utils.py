from datetime import datetime, timedelta
from fastapi import HTTPException
from app.utils import dynamodb_utils
from app.logger import get_logger

logger = get_logger(__name__)

def book_slot_logic(data):
    pk = f"{data.doctor_id}#{data.date}"
    sk = f"{data.start}#{data.user_id}"
    date_obj = datetime.strptime(data.date, "%Y-%m-%d")

    # Step 1: Fetch availability for that day
    day = date_obj.strftime("%A")
    availability_pk = f"{data.doctor_id}#{day}"
    res = dynamodb_utils.query_availability(availability_pk)

    # Check slot existence
    availability_sk = None
    for item in res.get("Items", []):
        start_time = item["SK"].split("-")[0]
        if start_time == data.start and item.get("available", True):
            availability_sk = item["SK"]
            break
    if not availability_sk:
        raise HTTPException(400, "Slot unavailable or not found")

    # Step 2: Check existing booking
    existing = dynamodb_utils.query_booking(pk, starts_with=data.start)
    for item in existing.get("Items", []):
        if item["user_id"] == data.user_id:
            raise HTTPException(400, "User already booked this slot")

    # Step 3: Save booking
    booking_item = {
        "PK": pk,
        "SK": sk,
        "doctor_id": data.doctor_id,
        "date": data.date,
        "start": data.start,
        "user_id": data.user_id
    }
    dynamodb_utils.put_booking_item(booking_item)

    # Step 4: Mark slot unavailable
    dynamodb_utils.update_availability(availability_pk, availability_sk, False)

    return {"message": "Slot booked successfully."}


def cancel_slot_logic(data):
    pk = f"{data.doctor_id}#{data.date}"
    sk = f"{data.start}#{data.user_id}"

    dynamodb_utils.delete_booking(pk, sk)

    date_obj = datetime.strptime(data.date, "%Y-%m-%d")
    day = date_obj.strftime("%A")
    availability_pk = f"{data.doctor_id}#{day}"

    res = dynamodb_utils.query_availability(availability_pk)
    availability_sk = None
    for item in res.get("Items", []):
        if item["SK"].split("-")[0] == data.start:
            availability_sk = item["SK"]
            break

    if availability_sk:
        dynamodb_utils.update_availability(availability_pk, availability_sk, True)

    return {"message": "Booking cancelled and slot released."}
