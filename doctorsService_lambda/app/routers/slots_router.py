from fastapi import APIRouter, HTTPException
from datetime import datetime, time
from app.models.schemas import DoctorSlotsSetRequest, DoctorSlotUpdateRequest
from app.utils.error_utils import handle_exception
from app.config import DOCTORS_TABLE, AVAILABILITY_TABLE
from app.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/doctorsService", tags=["Slots"])

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

@router.post("/setSlots")
def set_availability(data: DoctorSlotsSetRequest):
    try:
        doctor = DOCTORS_TABLE.get_item(Key={"doctor_id": data.doctor_id})
        if "Item" not in doctor:
            raise HTTPException(status_code=404, detail="Doctor not found")

        # Validate date format
        try:
            datetime.strptime(data.date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

        pk = data.doctor_id
        created_at = datetime.utcnow().isoformat()
        expiry_timestamp = get_expiry_timestamp(data.date)
        
        for slot in data.slots:
            # SK format: date#slot_time (e.g., "2025-11-28#10:00")
            sk = f"{data.date}#{slot.start}"
            AVAILABILITY_TABLE.put_item(Item={
                "PK": pk,
                "SK": sk,
                "available": True,
                "user_id": None,
                "booking_id": None,
                "created_at": created_at,
                "expiry_timestamp": expiry_timestamp
            })

        return {"message": f"Availability for {data.date} set successfully."}
    except Exception as e:
        handle_exception(e, "Set availability")

@router.post("/updateSlot")
def update_slot(data: DoctorSlotUpdateRequest):
    try:
        # Validate date format
        try:
            datetime.strptime(data.date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

        pk = data.doctor_id
        # SK format: date#slot_time (e.g., "2025-11-28#10:00")
        sk = f"{data.date}#{data.slot_time}"
        expiry_timestamp = get_expiry_timestamp(data.date)
        
        # Get existing item to preserve user_id and booking_id if they exist
        existing = AVAILABILITY_TABLE.get_item(Key={"PK": pk, "SK": sk})
        item = {
            "PK": pk,
            "SK": sk,
            "available": data.available,
            "expiry_timestamp": expiry_timestamp
        }
        
        if "Item" in existing:
            # Preserve existing attributes
            item["user_id"] = existing["Item"].get("user_id")
            item["booking_id"] = existing["Item"].get("booking_id")
            item["created_at"] = existing["Item"].get("created_at", datetime.utcnow().isoformat())
        else:
            # New item
            item["user_id"] = None
            item["booking_id"] = None
            item["created_at"] = datetime.utcnow().isoformat()
        
        AVAILABILITY_TABLE.put_item(Item=item)
        return {"message": f"Slot {'enabled' if data.available else 'disabled'} successfully"}
    except Exception as e:
        handle_exception(e, "Update slot")
