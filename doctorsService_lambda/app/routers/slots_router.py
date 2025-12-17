"""
Slots router - thin wrapper around slots service.
"""
from fastapi import APIRouter
from app.models.schemas import DoctorSlotsSetRequest, DoctorSlotUpdateRequest
from app.services.slots_service import set_availability, update_slot
from app.utils.error_utils import handle_exception

router = APIRouter(prefix="/doctorsService", tags=["Slots"])


@router.post("/setSlots")
def set_availability_endpoint(data: DoctorSlotsSetRequest):
    try:
        return set_availability(data.doctor_id, data.date, data.slots)
    except Exception as e:
        handle_exception(e, "Set availability")


@router.post("/updateSlot")
def update_slot_endpoint(data: DoctorSlotUpdateRequest):
    try:
        return update_slot(data.doctor_id, data.date, data.slot_time, data.available)
    except Exception as e:
        handle_exception(e, "Update slot")
