"""
Slots router - thin wrapper around slots service.
"""
from fastapi import APIRouter, Path
from app.models.schemas import DoctorSlotsSetRequest, DoctorSlotUpdateRequest
from app.services.slots_service import set_availability, update_slot, clear_all_slots
from app.utils.error_utils import handle_exception

router = APIRouter(prefix="/doctorsService", tags=["Slots"])


@router.post("/setSlots")
def set_availability_endpoint(data: DoctorSlotsSetRequest):
    """
    Set availability slots for a doctor for the next X days starting from today.
    Creates the same slots for each day.
    """
    try:
        return set_availability(data.doctor_id, data.days, data.slots)
    except Exception as e:
        handle_exception(e, "Set availability")


@router.post("/updateSlot")
def update_slot_endpoint(data: DoctorSlotUpdateRequest):
    try:
        return update_slot(data.doctor_id, data.date, data.slot_time, data.available)
    except Exception as e:
        handle_exception(e, "Update slot")


@router.delete("/doctors/{doctor_id}/slots")
def clear_all_slots_endpoint(doctor_id: str = Path(..., description="Doctor ID")):
    """
    Clear all availability slots for a doctor.
    This will delete all slots regardless of date or availability status.
    """
    try:
        return clear_all_slots(doctor_id)
    except Exception as e:
        handle_exception(e, "Clear all slots")
