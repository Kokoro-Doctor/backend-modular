from pydantic import BaseModel, Field, validator
from typing import Optional
from decimal import Decimal


class CreatePaymentLinkRequest(BaseModel):
    plan_id: str = Field(
        ...,
        description="Plan ID (human-readable format: PLAN_<PRICE>_<DURATION>D_<SCOPE>, e.g., PLAN_999_30D_ALL). "
        "Amount is derived from plan in database - plan_id is ONLY an identifier."
    )
    user_id: Optional[str] = Field(
        None, 
        description="User ID for subscription mapping."
    )
    doctor_id: Optional[str] = Field(
        None, 
        description="Doctor ID associated with this plan."
    )


class VerifyPaymentRequest(BaseModel):
    payment_id: str = Field(..., description="Razorpay payment ID")
    plan_id: Optional[str] = Field(
        None,
        description="Plan ID (human-readable format: PLAN_<PRICE>_<DURATION>D_<SCOPE>). "
        "Used to validate payment amount matches plan price from database."
    )
    user_id: Optional[str] = Field(None, description="User ID for subscription")
    doctor_id: Optional[str] = Field(None, description="Doctor ID for subscription")


class PaymentLinkResponse(BaseModel):
    message: str
    payment_link: str
    plan_id: Optional[str] = None
    amount: Optional[float] = None


class PaymentVerificationResponse(BaseModel):
    message: str
    order_id: str
    payment_id: str
    status: str
    invoice_url: Optional[str] = None
    subscription_id: Optional[str] = None
    subscription_message: Optional[str] = Field(
        None,
        description="Message about subscription creation status. "
        "Indicates if subscription was created, already exists, or creation failed."
    )

