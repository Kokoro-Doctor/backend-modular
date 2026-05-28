from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class PayoutMethod(str, Enum):
    BANK = "BANK"
    UPI = "UPI"


class PayoutStatus(str, Enum):
    REQUESTED = "REQUESTED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RequestPayoutRequest(BaseModel):
    doctor_id: str = Field(..., description="Doctor ID")
    payout_month: str = Field(..., description="Month in YYYY-MM format")
    payout_method: PayoutMethod = Field(..., description="Payout method (BANK or UPI)")


class UpdatePayoutStatusRequest(BaseModel):
    payout_id: str = Field(..., description="Payout ID")
    status: PayoutStatus = Field(..., description="New status")
    transaction_reference: Optional[str] = Field(None, description="Transaction reference")


class PayoutResponse(BaseModel):
    payout_id: str
    doctor_id: str
    payout_month: str
    total_amount: float
    status: str
    payout_method: str
    transaction_reference: Optional[str] = None
    requested_at: str
    processed_at: Optional[str] = None


class EarningsSummaryResponse(BaseModel):
    doctor_id: str
    month: Optional[str] = None
    total_gross: float
    total_platform_fee: float
    total_net: float
    available_amount: float
    paid_amount: float
    entry_count: int

