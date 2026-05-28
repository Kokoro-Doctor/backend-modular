"""
Payment Router - API endpoints for payment operations
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from app.models.schemas import (
    CreatePaymentLinkRequest,
    PaymentLinkResponse,
    PaymentVerificationResponse
)
from app.services.payment_service import create_payment_link, verify_payment, process_razorpay_webhook
from app.utils.error_utils import handle_exception
from app.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


# class PaymentRequest(BaseModel):
#     """Unified payment request model for backward compatibility"""
#     payment_id: Optional[str] = None
#     plan_id: Optional[str] = None
#     user_id: Optional[str] = None
#     doctor_id: Optional[str] = None
#     # amount is removed - backend fetches from plan_id to prevent tampering


# @router.post("", response_model=None, status_code=200)
# def process_payment(request: PaymentRequest):
#     """
#     Unified payment endpoint.
#     Routes to create_payment_link or verify_payment based on request body.
#     - If 'plan_id' is present and 'payment_id' is not: creates payment link
#     - If 'payment_id' is present: verifies payment
#     """
#     try:
#         # Route based on request body
#         if request.plan_id and not request.payment_id:
#             # Create payment link - plan_id is required, amount is derived from plan
#             result = create_payment_link(plan_id=request.plan_id)
#             return PaymentLinkResponse(**result)
            
#         elif request.payment_id:
#             # Verify payment
#             result = verify_payment(
#                 payment_id=request.payment_id,
#                 plan_id=request.plan_id,
#                 user_id=request.user_id,
#                 doctor_id=request.doctor_id
#             )
#             return PaymentVerificationResponse(**result)
#         else:
#             raise HTTPException(400, "Invalid request: must provide either 'plan_id' (to create payment link) or 'payment_id' (to verify payment)")
            
#     except HTTPException:
#         raise
#     except Exception as e:
#         handle_exception(e, "Process payment")


@router.post("/payment-link", response_model=PaymentLinkResponse, status_code=200)
def create_payment_link_endpoint(request: CreatePaymentLinkRequest):
    """
    Create a Razorpay payment link.
    Amount is derived from the plan_id - client-provided amounts are ignored for security.
    """
    try:
        result = create_payment_link(plan_id=request.plan_id, user_id=request.user_id, doctor_id=request.doctor_id)
        return PaymentLinkResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        handle_exception(e, "Create payment link")

@router.post("/webhook")
async def razorpay_webhook(request: Request):
    """
    Razorpay sends data here automatically when payment is captured.
    We verify the signature and update the database.
    """
    return await process_razorpay_webhook(request)


