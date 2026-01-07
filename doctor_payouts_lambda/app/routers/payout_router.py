"""
Payout Router - API endpoints for doctor payout management
"""
from fastapi import APIRouter, HTTPException, Path, Query
from typing import Optional, List
from app.models.schemas import (
    RequestPayoutRequest,
    UpdatePayoutStatusRequest,
    PayoutResponse,
    EarningsSummaryResponse
)
from app.services.payout_service import (
    request_payout,
    get_payout_by_id,
    get_doctor_payouts,
    update_payout_status,
    get_all_pending_payouts
)
from app.services.earnings_summary_service import get_doctor_earnings_summary
from app.utils.error_utils import handle_exception
from app.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/payouts", tags=["Doctor Payouts"])


@router.post("/request", response_model=PayoutResponse, status_code=201)
def request_payout_endpoint(request: RequestPayoutRequest):
    """
    Request a payout for a specific month.
    Only one payout per doctor per month is allowed.
    Withdrawal is only allowed after month end.
    """
    try:
        payout = request_payout(
            doctor_id=request.doctor_id,
            payout_month=request.payout_month,
            payout_method=request.payout_method.value
        )
        return PayoutResponse(**payout)
    except HTTPException:
        raise
    except Exception as e:
        handle_exception(e, "Request payout")


@router.get("/earnings/summary", response_model=EarningsSummaryResponse)
def get_earnings_summary_endpoint(
    doctor_id: str = Query(..., description="Doctor ID"),
    month: Optional[str] = Query(None, description="Month in YYYY-MM format (optional)")
):
    """
    Get earnings summary for a doctor.
    If month is provided, returns summary for that month only.
    Otherwise returns total summary across all months.
    """
    try:
        summary = get_doctor_earnings_summary(doctor_id=doctor_id, month=month)
        return EarningsSummaryResponse(**summary)
    except HTTPException:
        raise
    except Exception as e:
        handle_exception(e, "Get earnings summary")


@router.get("/{payout_id}", response_model=PayoutResponse)
def get_payout_endpoint(payout_id: str = Path(..., description="Payout ID")):
    """
    Get a payout by ID.
    """
    try:
        payout = get_payout_by_id(payout_id)
        if not payout:
            raise HTTPException(404, "Payout not found")
        return PayoutResponse(**payout)
    except HTTPException:
        raise
    except Exception as e:
        handle_exception(e, "Get payout")


@router.get("/doctors/{doctor_id}/history", response_model=List[PayoutResponse])
def get_doctor_payout_history_endpoint(doctor_id: str = Path(..., description="Doctor ID")):
    """
    Get payout history for a doctor.
    """
    try:
        payouts = get_doctor_payouts(doctor_id)
        return [PayoutResponse(**payout) for payout in payouts]
    except HTTPException:
        raise
    except Exception as e:
        handle_exception(e, "Get doctor payout history")


# Admin endpoints
@router.put("/admin/update-status", response_model=PayoutResponse)
def update_payout_status_endpoint(request: UpdatePayoutStatusRequest):
    """
    Update payout status (admin only).
    Used to mark payouts as PROCESSING, COMPLETED, or FAILED.
    """
    try:
        payout = update_payout_status(
            payout_id=request.payout_id,
            status=request.status.value,
            transaction_reference=request.transaction_reference
        )
        return PayoutResponse(**payout)
    except HTTPException:
        raise
    except Exception as e:
        handle_exception(e, "Update payout status")


@router.get("/admin/pending", response_model=List[PayoutResponse])
def get_pending_payouts_endpoint():
    """
    Get all pending payouts (admin only).
    Returns payouts with status REQUESTED or PROCESSING.
    """
    try:
        payouts = get_all_pending_payouts()
        return [PayoutResponse(**payout) for payout in payouts]
    except HTTPException:
        raise
    except Exception as e:
        handle_exception(e, "Get pending payouts")

