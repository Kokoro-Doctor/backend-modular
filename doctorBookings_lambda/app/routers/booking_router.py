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
        date_obj = datetime.strptime(data.date, "%Y-%m-%d")
        day = date_obj.strftime("%A")
        pk = f"{data.doctor_id}#{day}"

        res = dynamodb_utils.query_availability(pk)
        slots = [
            {
                "start": i["SK"].split("-")[0],
                "end": i["SK"].split("-")[1],
                "available": i.get("available", True)
            }
            for i in res.get("Items", [])
        ]
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
