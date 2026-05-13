from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime
from enum import Enum
from decimal import Decimal

# ==================== Booking Request Models ====================

class BookSlotRequest(BaseModel):
    doctor_id: str = Field(..., description="Doctor identifier")
    date: str = Field(..., description="Date in YYYY-MM-DD format")
    start_time: str = Field(..., description="Start time in HH:MM format")
    user_id: str = Field(..., description="User identifier")
    
    @field_validator('date')
    @classmethod
    def validate_date(cls, v):
        try:
            datetime.strptime(v, "%Y-%m-%d")
            return v
        except ValueError:
            raise ValueError("Invalid date format. Use YYYY-MM-DD")
    
    @field_validator('start_time')
    @classmethod
    def validate_time(cls, v):
        try:
            datetime.strptime(v, "%H:%M")
            return v
        except ValueError:
            raise ValueError("Invalid time format. Use HH:MM")


class BookingType(str, Enum):
    upcoming = "upcoming"
    past = "past"


# ==================== Booking Response Models ====================

class SlotAvailabilityResponse(BaseModel):
    slot_time: str
    available: bool
    booking_id: Optional[str] = None
    user_id: Optional[str] = None


class BookingResponse(BaseModel):
    booking_id: str
    doctor_id: str
    date: str
    start_time: str
    user_id: str
    meet_link: Optional[str] = None
    created_at: str
    status: Optional[str] = None
    subscription_id: Optional[str] = None


class CalendarSlotResponse(BaseModel):
    date: str
    slot_time: str
    available: bool
    booking_id: Optional[str] = None
    user_id: Optional[str] = None


class CalendarResponse(BaseModel):
    doctor_id: str
    days: int
    slots: List[CalendarSlotResponse]


# ==================== Subscription Enums ====================

class SubscriptionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    EXHAUSTED = "EXHAUSTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


# ==================== Subscription Plan Models ====================

class SubscriptionPlanCreate(BaseModel):
    doctor_id: str = Field(..., description="Doctor ID or 'ALL' for global plans")
    price: Decimal = Field(..., gt=0, description="Plan price")
    appointments_allowed: int = Field(..., gt=0, description="Number of appointments allowed")
    validity_days: int = Field(..., gt=0, description="Validity period in days")
    valid_from: str = Field(..., description="Valid from date (ISO format)")
    valid_to: Optional[str] = Field(None, description="Valid to date (ISO format, nullable)")
    is_active: bool = Field(True, description="Whether plan is active")
    plan_id: Optional[str] = Field(
        None,
        description="Optional human-readable plan_id. If not provided, auto-generates using format: PLAN_<PRICE>_<DURATION>D_<SCOPE>. "
        "IMPORTANT: plan_id is ONLY an identifier. The database is ALWAYS the source of truth for plan attributes."
    )
    
    @field_validator('price', mode='before')
    @classmethod
    def validate_price(cls, v):
        # Convert float/string to Decimal for DynamoDB compatibility
        if isinstance(v, float):
            return Decimal(str(v))
        if isinstance(v, str):
            return Decimal(v)
        return v
    
    @field_validator('doctor_id')
    @classmethod
    def validate_doctor_id(cls, v):
        if v != "ALL" and not v:
            raise ValueError("doctor_id must be 'ALL' or a valid doctor ID")
        return v
    
    @field_validator('valid_from', 'valid_to')
    @classmethod
    def validate_date(cls, v, info):
        if v is None:
            return v
        try:
            datetime.fromisoformat(v.replace('Z', '+00:00'))
            return v
        except ValueError:
            raise ValueError(f"Invalid date format for {info.field_name}. Use ISO format.")


class SubscriptionPlanResponse(BaseModel):
    plan_id: str
    doctor_id: str
    price: Decimal
    appointments_allowed: int
    validity_days: int
    valid_from: str
    valid_to: Optional[str]
    is_active: bool
    created_at: str


class SubscriptionPlanUpdate(BaseModel):
    price: Optional[Decimal] = Field(None, gt=0)
    appointments_allowed: Optional[int] = Field(None, gt=0)
    validity_days: Optional[int] = Field(None, gt=0)
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    is_active: Optional[bool] = None
    
    @field_validator('price', mode='before')
    @classmethod
    def validate_price(cls, v):
        # Convert float/string to Decimal for DynamoDB compatibility
        if v is None:
            return v
        if isinstance(v, float):
            return Decimal(str(v))
        if isinstance(v, str):
            return Decimal(v)
        return v


# ==================== User Subscription Models ====================

class CreateSubscriptionRequest(BaseModel):
    user_id: str = Field(..., description="User ID")
    doctor_id: str = Field(..., description="Doctor ID")
    plan_id: str = Field(..., description="Plan ID")
    payment_id: str = Field(..., description="Payment ID from payment gateway")


class SubscriptionResponse(BaseModel):
    subscription_id: str
    user_id: str
    doctor_id: str
    plan_id: str
    plan_price: Decimal
    appointments_total: int
    appointments_used: int
    status: SubscriptionStatus
    start_date: str
    end_date: str
    payment_id: str
    created_at: str


class SubscriptionValidationResponse(BaseModel):
    is_valid: bool
    subscription_id: Optional[str] = None
    appointments_remaining: Optional[int] = None
    status: Optional[str] = None
    message: str


class IncrementAppointmentsRequest(BaseModel):
    subscription_id: str
    user_id: str
    doctor_id: str


class CreateTestSubscriptionRequest(BaseModel):
    user_id: str = Field(..., description="User ID")
    doctor_id: str = Field(..., description="Doctor ID")
    plan_id: str = Field(..., description="Plan ID")

