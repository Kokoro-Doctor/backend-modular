from pydantic import BaseModel

class BookSlotInput(BaseModel):
    doctor_id: str
    date: str  # YYYY-MM-DD
    start: str  # HH:MM
    user_id: str

class CancelSlotInput(BookSlotInput):
    pass

class AvailableSlotsRequest(BaseModel):
    doctor_id: str
    date: str  # YYYY-MM-DD

class FetchBookingsRequest(BaseModel):
    id: str
    type: str  # "user" or "doctor"
    days: int
