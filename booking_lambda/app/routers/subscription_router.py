"""
Subscription Router - API endpoints for subscription management
"""
from fastapi import APIRouter, HTTPException, Path, Query
from typing import Optional, List
from app.models.schemas import (
    SubscriptionPlanCreate,
    SubscriptionPlanResponse,
    SubscriptionPlanUpdate,
    CreateSubscriptionRequest,
    SubscriptionResponse,
    SubscriptionValidationResponse,
    IncrementAppointmentsRequest
)
from app.services.subscription_plan_service import (
    create_subscription_plan,
    get_subscription_plan,
    get_active_plans_for_doctor,
    get_all_active_plans,
    update_subscription_plan
)
from app.services.user_subscription_service import (
    create_user_subscription,
    get_user_subscription,
    get_user_subscriptions,
    get_doctor_subscribers,
    validate_subscription_for_booking,
    increment_appointments_used,
    cancel_subscription
)
from app.utils.error_utils import handle_exception
from app.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/booking", tags=["Subscription"])


# ==================== Subscription Plan Endpoints ====================

@router.post("/plans", response_model=SubscriptionPlanResponse, status_code=201)
def create_plan(plan_data: SubscriptionPlanCreate):
    """
    Create a new subscription plan with a human-readable plan_id.
    Plans can be global (doctor_id='ALL') or doctor-specific.
    
    If plan_id is provided, it must match the format PLAN_<PRICE>_<DURATION>D_<SCOPE>
    and match the plan data. If not provided, plan_id is auto-generated.
    
    IMPORTANT: plan_id is ONLY an identifier. The database is ALWAYS the source of truth.
    """
    try:
        plan = create_subscription_plan(plan_data, plan_id=plan_data.plan_id)
        return SubscriptionPlanResponse(**plan)
    except HTTPException:
        raise
    except Exception as e:
        handle_exception(e, "Create subscription plan")


@router.get("/plans/{plan_id}", response_model=SubscriptionPlanResponse)
def get_plan(plan_id: str = Path(..., description="Plan ID (human-readable format: PLAN_<PRICE>_<DURATION>D_<SCOPE>)")):
    """
    Get a subscription plan by ID.
    
    IMPORTANT: Always fetches plan data from database. Never derives plan attributes
    from plan_id format. The database is the source of truth.
    """
    try:
        plan = get_subscription_plan(plan_id)
        if not plan:
            raise HTTPException(404, "Subscription plan not found")
        return SubscriptionPlanResponse(**plan)
    except HTTPException:
        raise
    except Exception as e:
        handle_exception(e, "Get subscription plan")


@router.get("/plans", response_model=List[SubscriptionPlanResponse])
def list_plans(
    doctor_id: Optional[str] = Query(None, description="Filter by doctor ID or 'ALL' for global plans")
):
    """
    List subscription plans.
    If doctor_id provided, returns active plans for that doctor (including global plans).
    Otherwise returns all active plans.
    """
    try:
        if doctor_id:
            plans = get_active_plans_for_doctor(doctor_id)
        else:
            plans = get_all_active_plans()
        return [SubscriptionPlanResponse(**plan) for plan in plans]
    except HTTPException:
        raise
    except Exception as e:
        handle_exception(e, "List subscription plans")


@router.put("/plans/{plan_id}", response_model=SubscriptionPlanResponse)
def update_plan(
    plan_id: str = Path(..., description="Plan ID"),
    update_data: SubscriptionPlanUpdate = ...
):
    """
    Update a subscription plan.
    Note: Consider creating new plan versions for immutability.
    """
    try:
        plan = update_subscription_plan(plan_id, update_data)
        return SubscriptionPlanResponse(**plan)
    except HTTPException:
        raise
    except Exception as e:
        handle_exception(e, "Update subscription plan")


# ==================== User Subscription Endpoints ====================

@router.post("/subscriptions", response_model=SubscriptionResponse, status_code=201)
def create_subscription(request: CreateSubscriptionRequest):
    """
    Create a user subscription after successful payment.
    This endpoint should be called by the payment service after payment verification.
    """
    try:
        subscription = create_user_subscription(
            user_id=request.user_id,
            doctor_id=request.doctor_id,
            plan_id=request.plan_id,
            payment_id=request.payment_id
        )
        return SubscriptionResponse(**subscription)
    except HTTPException:
        raise
    except Exception as e:
        handle_exception(e, "Create subscription")


@router.get("/subscriptions/{subscription_id}", response_model=SubscriptionResponse)
def get_subscription(subscription_id: str = Path(..., description="Subscription ID")):
    """
    Get a subscription by ID.
    """
    try:
        subscription = get_user_subscription(subscription_id)
        if not subscription:
            raise HTTPException(404, "Subscription not found")
        return SubscriptionResponse(**subscription)
    except HTTPException:
        raise
    except Exception as e:
        handle_exception(e, "Get subscription")


@router.get("/users/{user_id}/subscriptions", response_model=List[SubscriptionResponse])
def get_user_subscriptions_endpoint(user_id: str = Path(..., description="User ID")):
    """
    Get all subscriptions for a user.
    """
    try:
        subscriptions = get_user_subscriptions(user_id)
        return [SubscriptionResponse(**sub) for sub in subscriptions]
    except HTTPException:
        raise
    except Exception as e:
        handle_exception(e, "Get user subscriptions")


@router.get("/doctors/{doctor_id}/subscribers", response_model=List[SubscriptionResponse])
def get_doctor_subscribers_endpoint(doctor_id: str = Path(..., description="Doctor ID")):
    """
    Get all subscribers for a doctor.
    """
    try:
        subscriptions = get_doctor_subscribers(doctor_id)
        return [SubscriptionResponse(**sub) for sub in subscriptions]
    except HTTPException:
        raise
    except Exception as e:
        handle_exception(e, "Get doctor subscribers")


@router.get("/subscriptions/validate", response_model=SubscriptionValidationResponse)
def validate_subscription(
    user_id: str = Query(..., description="User ID"),
    doctor_id: str = Query(..., description="Doctor ID")
):
    """
    Validate if user has an active subscription for booking.
    Used by booking service before allowing appointment booking.
    """
    try:
        validation = validate_subscription_for_booking(user_id, doctor_id)
        return SubscriptionValidationResponse(**validation)
    except HTTPException:
        raise
    except Exception as e:
        handle_exception(e, "Validate subscription")


@router.post("/subscriptions/increment", response_model=SubscriptionResponse)
def increment_appointments(request: IncrementAppointmentsRequest):
    """
    Atomically increment appointments_used for a subscription.
    Called by booking service after successful appointment booking.
    """
    try:
        subscription = increment_appointments_used(
            subscription_id=request.subscription_id,
            user_id=request.user_id,
            doctor_id=request.doctor_id
        )
        return SubscriptionResponse(**subscription)
    except HTTPException:
        raise
    except Exception as e:
        handle_exception(e, "Increment appointments")


@router.post("/subscriptions/{subscription_id}/cancel", response_model=SubscriptionResponse)
def cancel_subscription_endpoint(
    subscription_id: str = Path(..., description="Subscription ID"),
    user_id: str = Query(..., description="User ID")
):
    """
    Cancel a subscription.
    """
    try:
        subscription = cancel_subscription(subscription_id, user_id)
        return SubscriptionResponse(**subscription)
    except HTTPException:
        raise
    except Exception as e:
        handle_exception(e, "Cancel subscription")

