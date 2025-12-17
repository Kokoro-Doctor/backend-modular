from fastapi import APIRouter, HTTPException, Query, Path
from datetime import datetime
from typing import Optional
from app.models.schemas import (
    BookSlotRequest,
    BookingResponse,
    BookingType,
    SlotAvailabilityResponse,
    CalendarResponse,
    CalendarSlotResponse
)
from app.services.booking_service import (
    book_slot_atomically,
    cancel_booking_by_id,
    get_doctor_bookings_service,
    get_user_bookings_service,
    get_doctor_availability_service,
    get_doctor_calendar_service
)
from app.utils import error_utils
from app.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/booking", tags=["Appointment"])


# ==================== Appointment Endpoints ====================

@router.post("/bookings", response_model=BookingResponse, status_code=201)
def create_booking(request: BookSlotRequest):
    """
    Book a slot atomically.
    Checks availability and creates booking if available.
    Validates subscription before booking.
    """
    try:
        result = book_slot_atomically(
            doctor_id=request.doctor_id,
            date=request.date,
            start_time=request.start_time,
            user_id=request.user_id
        )
        return BookingResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        error_utils.handle_exception(e, "Create booking")


@router.delete("/bookings/{booking_id}", status_code=200)
def cancel_booking(booking_id: str = Path(..., description="Booking ID to cancel")):
    """
    Cancel a booking by booking_id.
    Marks slot as available and removes booking.
    """
    try:
        result = cancel_booking_by_id(booking_id)
        return result
    except HTTPException:
        raise
    except Exception as e:
        error_utils.handle_exception(e, "Cancel booking")


@router.get("/doctors/{doctor_id}/bookings", response_model=list[BookingResponse])
def get_doctor_bookings(
    doctor_id: str = Path(..., description="Doctor ID"),
    date: Optional[str] = Query(None, description="Filter by date (YYYY-MM-DD)")
):
    """
    Get bookings for a doctor.
    If date provided, returns bookings for that date only.
    Returns sorted by time.
    """
    try:
        if date:
            # Validate date format
            try:
                datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD")
        
        bookings = get_doctor_bookings_service(doctor_id, date)
        return [BookingResponse(**booking) for booking in bookings]
    except HTTPException:
        raise
    except Exception as e:
        error_utils.handle_exception(e, "Fetch doctor bookings")


@router.get("/users/{user_id}/bookings", response_model=list[BookingResponse])
def get_user_bookings(
    user_id: str = Path(..., description="User ID"),
    type: Optional[BookingType] = Query(None, description="Filter by type: upcoming or past")
):
    """
    Get bookings for a user.
    Uses GSI on user_id.
    If type=upcoming → returns only future bookings.
    If type=past → returns only past bookings.
    Returns sorted by date and time.
    """
    try:
        booking_type = type.value if type else None
        bookings = get_user_bookings_service(user_id, booking_type)
        return [BookingResponse(**booking) for booking in bookings]
    except HTTPException:
        raise
    except Exception as e:
        error_utils.handle_exception(e, "Fetch user bookings")


@router.get("/doctors/{doctor_id}/availability", response_model=list[SlotAvailabilityResponse])
def get_doctor_availability(
    doctor_id: str = Path(..., description="Doctor ID"),
    date: str = Query(..., description="Date in YYYY-MM-DD format")
):
    """
    Get slot availability for a doctor on a specific date.
    Returns slot_time, available status, and booking_id if booked.
    """
    try:
        # Validate date format
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD")
        
        slots = get_doctor_availability_service(doctor_id, date)
        return [SlotAvailabilityResponse(**slot) for slot in slots]
    except HTTPException:
        raise
    except Exception as e:
        error_utils.handle_exception(e, "Fetch availability")


@router.get("/doctors/{doctor_id}/calendar", response_model=CalendarResponse)
def get_doctor_calendar(
    doctor_id: str = Path(..., description="Doctor ID"),
    days: int = Query(7, ge=1, le=30, description="Number of days to include")
):
    """
    Get unified calendar for a doctor showing availability + bookings for next N days.
    Merges data from both AvailabilityTable and BookingsTable.
    """
    try:
        result = get_doctor_calendar_service(doctor_id, days)
        return CalendarResponse(
            doctor_id=result["doctor_id"],
            days=result["days"],
            slots=[CalendarSlotResponse(**slot) for slot in result["slots"]]
        )
    except HTTPException:
        raise
    except Exception as e:
        error_utils.handle_exception(e, "Fetch calendar")

