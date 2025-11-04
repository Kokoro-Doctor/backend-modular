from fastapi import APIRouter, HTTPException
from app.models.schemas import DoctorSlotsSetRequest, DoctorSlotUpdateRequest
from app.utils.error_utils import handle_exception
from app.config import DOCTORS_TABLE, AVAILABILITY_TABLE
from app.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/doctorsService", tags=["Slots"])

@router.post("/setSlots")
def set_availability(data: DoctorSlotsSetRequest):
    try:
        doctor = DOCTORS_TABLE.get_item(Key={"email": data.doctor_id})
        if "Item" not in doctor:
            raise HTTPException(status_code=404, detail="Doctor not found")

        pk = f"{data.doctor_id}#{data.day.value}"
        for slot in data.slots:
            sk = f"{slot.start}-{slot.end}"
            AVAILABILITY_TABLE.put_item(Item={"PK": pk, "SK": sk, "available": True})

        return {"message": f"Availability for {data.day.value} set successfully."}
    except Exception as e:
        handle_exception(e, "Set availability")

@router.post("/updateSlot")
def update_slot(data: DoctorSlotUpdateRequest):
    try:
        pk = f"{data.doctor_id}#{data.day.value}"
        sk = f"{data.slot.start}-{data.slot.end}"
        AVAILABILITY_TABLE.put_item(Item={"PK": pk, "SK": sk, "available": data.available})
        return {"message": f"Slot {'enabled' if data.available else 'disabled'} successfully"}
    except Exception as e:
        handle_exception(e, "Update slot")
