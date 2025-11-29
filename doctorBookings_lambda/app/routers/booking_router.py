from fastapi import APIRouter, HTTPException
from datetime import datetime, timedelta
from app.models.schemas import (
    BookSlotInput, CancelSlotInput, AvailableSlotsRequest, FetchBookingsRequest
)
from app.utils import slot_utils, dynamodb_utils, error_utils
from app.config import BOOKING_TABLE
from boto3.dynamodb.conditions import Key
from app.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

@router.post("/book")
def book_slot(data: BookSlotInput):
    try:
        return slot_utils.book_slot_logic(data)
    except HTTPException as he:
        raise he
    except Exception as e:
        error_utils.handle_exception(e, "Booking")


@router.post("/cancel")
def cancel_slot(data: CancelSlotInput):
    try:
        return slot_utils.cancel_slot_logic(data)
    except Exception as e:
        error_utils.handle_exception(e, "Cancellation")


@router.post("/available")
def get_available_slots(data: AvailableSlotsRequest):
    try:
        # Validate date format
        try:
            datetime.strptime(data.date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD")

        # Query availability for the specific date
        res = dynamodb_utils.query_availability(data.doctor_id, data.date)
        
        slots = []
        for item in res.get("Items", []):
            # SK format: date#slot_time (e.g., "2025-11-28#10:00")
            sk_parts = item["SK"].split("#")
            if len(sk_parts) == 2:
                slot_time = sk_parts[1]
                slots.append({
                    "start": slot_time,
                    "available": item.get("available", True),
                    "user_id": item.get("user_id"),
                    "booking_id": item.get("booking_id")
                })
        
        return {"slots": slots}
    except Exception as e:
        error_utils.handle_exception(e, "Fetch available slots")


@router.post("/fetchBookings")
def fetch_bookings(data: FetchBookingsRequest):
    try:
        today = datetime.utcnow().date()
        all_bookings = []

        if data.type == "doctor":
            for i in range(data.days):
                date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
                pk = f"{data.id}#{date}"
                res = BOOKING_TABLE.query(KeyConditionExpression=Key("PK").eq(pk))
                all_bookings.extend(res.get("Items", []))

        elif data.type == "user":
            res = BOOKING_TABLE.query(
                IndexName="GSI_UserBookings",
                KeyConditionExpression=Key("user_id").eq(data.id)
            )
            for item in res.get("Items", []):
                booking_date = datetime.strptime(item["date"], "%Y-%m-%d").date()
                if (today - booking_date).days <= data.days:
                    all_bookings.append(item)
        else:
            raise HTTPException(400, "Invalid type: must be 'doctor' or 'user'")

        return {"bookings": all_bookings}
    except Exception as e:
        error_utils.handle_exception(e, "Fetch bookings")
