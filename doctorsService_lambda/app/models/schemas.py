from pydantic import BaseModel
from typing import Optional, List
from enum import Enum

class UploadDoc(BaseModel):
    filename: str
    base64_content: str

class DoctorProfileUpdate(BaseModel):
    doctor_id: str
    description: Optional[str] = None
    specialization: Optional[str] = None
    experience: Optional[str] = None
    fees: Optional[int] = None
    timings: Optional[str] = None
    licenseNumber: Optional[str] = None
    registrationId: Optional[str] = None
    affiliation: Optional[str] = None
    degreeCertificate: Optional[UploadDoc] = None
    govtIdProof: Optional[UploadDoc] = None
    profilePhoto: Optional[UploadDoc] = None

class FetchDoctorsRequest(BaseModel):
    category: Optional[str] = None

class AvailabilitySlot(BaseModel):
    start: str
    end: str

class WeekDay(str, Enum):
    Monday = "Monday"
    Tuesday = "Tuesday"
    Wednesday = "Wednesday"
    Thursday = "Thursday"
    Friday = "Friday"
    Saturday = "Saturday"
    Sunday = "Sunday"

class DoctorSlotsSetRequest(BaseModel):
    doctor_id: str
    date: str  # YYYY-MM-DD format
    slots: List[AvailabilitySlot]

class DoctorSlotUpdateRequest(BaseModel):
    doctor_id: str
    date: str  # YYYY-MM-DD format
    slot_time: str  # HH:MM format (e.g., "10:00")
    available: Optional[bool] = True
