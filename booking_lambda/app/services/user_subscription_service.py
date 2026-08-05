"""
User Subscription Service - manages user subscriptions to doctors
"""
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from decimal import Decimal
from fastapi import HTTPException
from botocore.exceptions import ClientError
from app.config import USER_DOCTOR_SUBSCRIPTIONS_TABLE, SUBSCRIPTION_PLANS_TABLE
from app.models.schemas import SubscriptionStatus
from app.services.subscription_plan_service import get_subscription_plan
from app.services.user_doctor_relation_service import sync_relation_for_subscription
from app.logger import get_logger
from boto3.dynamodb.conditions import Key
import uuid

logger = get_logger(__name__)


def get_subscription_by_payment_id(payment_id: str) -> Optional[dict]:
    """
    Get subscription by payment_id using GSI.
    Returns the subscription if found, None otherwise.
    """
    try:
        response = USER_DOCTOR_SUBSCRIPTIONS_TABLE.query(
            IndexName="GSI_PaymentSubscription",
            KeyConditionExpression=Key("payment_id").eq(payment_id)
        )
        items = response.get("Items", [])
        if items:
            # Return the first subscription found (should be only one per payment_id)
            return items[0]
        return None
    except ClientError as e:
        logger.error(f"DynamoDB error fetching subscription by payment_id: {e}")
        raise HTTPException(500, "Failed to fetch subscription by payment_id")


def create_user_subscription(
    user_id: str,
    doctor_id: str,
    plan_id: str,
    payment_id: str
) -> dict:
    """
    Create a user subscription after successful payment.
    Idempotent: checks for existing subscription with same payment_id first.
    Uses conditional writes to protect against race conditions.
    Fetches plan details and creates subscription with snapshot of plan data.
    """
    # Check if subscription already exists for this payment_id
    existing_subscription = get_subscription_by_payment_id(payment_id)
    if existing_subscription:
        logger.info(f"Subscription already exists for payment_id {payment_id}: {existing_subscription.get('subscription_id')}")
        sync_relation_for_subscription(
            existing_subscription.get("user_id", user_id),
            existing_subscription.get("doctor_id", doctor_id),
            existing_subscription["subscription_id"],
        )
        return existing_subscription
    
    # Fetch plan details
    plan = get_subscription_plan(plan_id)
    if not plan:
        raise HTTPException(404, "Subscription plan not found")
    
    if not plan.get("is_active", False):
        raise HTTPException(400, "Subscription plan is not active")
    
    # Check if plan is valid for this doctor
    plan_doctor_id = plan.get("doctor_id")
    if plan_doctor_id != "ALL" and plan_doctor_id != doctor_id:
        raise HTTPException(400, "Plan is not valid for this doctor")
    
    # Check date validity
    now = datetime.now(timezone.utc)
    valid_from_str = plan.get("valid_from")
    valid_to_str = plan.get("valid_to")
    
    if valid_from_str:
        try:
            valid_from = datetime.fromisoformat(valid_from_str.replace('Z', '+00:00'))
            if now < valid_from:
                raise HTTPException(400, "Plan is not yet valid")
        except ValueError:
            logger.warning(f"Invalid valid_from date in plan {plan_id}")
    
    if valid_to_str:
        try:
            valid_to = datetime.fromisoformat(valid_to_str.replace('Z', '+00:00'))
            if now > valid_to:
                raise HTTPException(400, "Plan has expired")
        except ValueError:
            logger.warning(f"Invalid valid_to date in plan {plan_id}")
    
    # Create subscription
    subscription_id = str(uuid.uuid4())
    now_iso = now.isoformat()
    validity_days = plan.get("validity_days", 30)
    # Convert to int in case DynamoDB returns Decimal
    # Handle None, Decimal, int, or float types
    if validity_days is None:
        validity_days = 30
    else:
        validity_days = int(float(validity_days))  # Convert Decimal/float to int via float
    end_date = (now + timedelta(days=validity_days)).isoformat()
    
    subscription_item = {
        "subscription_id": subscription_id,
        "user_id": user_id,
        "doctor_id": doctor_id,
        "plan_id": plan_id,
        "plan_price": Decimal(str(plan.get("price", 0))),  # Snapshot - DynamoDB requires Decimal
        "appointments_total": int(plan.get("appointments_allowed", 0)),  # Snapshot
        "appointments_used": 0,
        "status": SubscriptionStatus.ACTIVE.value,
        "start_date": now_iso,
        "end_date": end_date,
        "payment_id": payment_id,
        "created_at": now_iso
    }
    
    try:
        # Use conditional write to ensure idempotency: only create if subscription_id doesn't exist
        # This protects against race conditions where multiple requests try to create simultaneously
        USER_DOCTOR_SUBSCRIPTIONS_TABLE.put_item(
            Item=subscription_item,
            ConditionExpression="attribute_not_exists(subscription_id)"
        )
        logger.info(f"Created subscription: {subscription_id} for user {user_id} to doctor {doctor_id} with payment_id {payment_id}")
        sync_relation_for_subscription(user_id, doctor_id, subscription_id)
        return subscription_item
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "ConditionalCheckFailedException":
            # Another request created the subscription concurrently, fetch and return it
            logger.warning(f"Conditional check failed for subscription creation, fetching existing subscription for payment_id {payment_id}")
            existing_subscription = get_subscription_by_payment_id(payment_id)
            if existing_subscription:
                sync_relation_for_subscription(
                    existing_subscription.get("user_id", user_id),
                    existing_subscription.get("doctor_id", doctor_id),
                    existing_subscription["subscription_id"],
                )
                return existing_subscription
            # If still not found, it might be a different error, re-raise
            logger.error(f"Subscription not found after conditional check failed: {e}")
            raise HTTPException(500, "Failed to create subscription: race condition detected")
        logger.error(f"DynamoDB error creating subscription: {e}")
        raise HTTPException(500, "Failed to create subscription")


def create_test_subscription(
    user_id: str,
    doctor_id: str,
    plan_id: str
) -> dict:
    """
    Create a test subscription directly without payment verification.
    This is for testing purposes only and should be protected by admin authentication.
    Uses a test payment_id format: TEST_<timestamp>_<uuid>
    """
    # Fetch plan details
    plan = get_subscription_plan(plan_id)
    if not plan:
        raise HTTPException(404, "Subscription plan not found")
    
    if not plan.get("is_active", False):
        raise HTTPException(400, "Subscription plan is not active")
    
    # Check if plan is valid for this doctor
    plan_doctor_id = plan.get("doctor_id")
    if plan_doctor_id != "ALL" and plan_doctor_id != doctor_id:
        raise HTTPException(400, "Plan is not valid for this doctor")
    
    # Check date validity
    now = datetime.now(timezone.utc)
    valid_from_str = plan.get("valid_from")
    valid_to_str = plan.get("valid_to")
    
    if valid_from_str:
        try:
            valid_from = datetime.fromisoformat(valid_from_str.replace('Z', '+00:00'))
            if now < valid_from:
                raise HTTPException(400, "Plan is not yet valid")
        except ValueError:
            logger.warning(f"Invalid valid_from date in plan {plan_id}")
    
    if valid_to_str:
        try:
            valid_to = datetime.fromisoformat(valid_to_str.replace('Z', '+00:00'))
            if now > valid_to:
                raise HTTPException(400, "Plan has expired")
        except ValueError:
            logger.warning(f"Invalid valid_to date in plan {plan_id}")
    
    # Generate test payment_id
    test_payment_id = f"TEST_{int(now.timestamp())}_{uuid.uuid4().hex[:8]}"
    
    # Create subscription
    subscription_id = str(uuid.uuid4())
    now_iso = now.isoformat()
    validity_days = plan.get("validity_days", 30)
    # Convert to int in case DynamoDB returns Decimal
    if validity_days is None:
        validity_days = 30
    else:
        validity_days = int(float(validity_days))
    end_date = (now + timedelta(days=validity_days)).isoformat()
    
    subscription_item = {
        "subscription_id": subscription_id,
        "user_id": user_id,
        "doctor_id": doctor_id,
        "plan_id": plan_id,
        "plan_price": Decimal(str(plan.get("price", 0))),
        "appointments_total": int(plan.get("appointments_allowed", 0)),
        "appointments_used": 0,
        "status": SubscriptionStatus.ACTIVE.value,
        "start_date": now_iso,
        "end_date": end_date,
        "payment_id": test_payment_id,
        "created_at": now_iso
    }
    
    try:
        # Use conditional write to ensure idempotency
        USER_DOCTOR_SUBSCRIPTIONS_TABLE.put_item(
            Item=subscription_item,
            ConditionExpression="attribute_not_exists(subscription_id)"
        )
        logger.info(f"Created test subscription: {subscription_id} for user {user_id} to doctor {doctor_id} with test payment_id {test_payment_id}")
        sync_relation_for_subscription(user_id, doctor_id, subscription_id)
        return subscription_item
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "ConditionalCheckFailedException":
            logger.warning(f"Conditional check failed for test subscription creation: {e}")
            raise HTTPException(500, "Failed to create test subscription: subscription_id already exists")
        logger.error(f"DynamoDB error creating test subscription: {e}")
        raise HTTPException(500, "Failed to create test subscription")


def create_import_subscription(
    user_id: str,
    doctor_id: str,
    plan_id: Optional[str] = None
) -> dict:
    """
    Create a subscription for doctor-imported patients (no payment).
    If plan_id is provided, uses create_test_subscription.
    If plan_id is not provided, creates a minimal plan-less subscription.
    """
    if plan_id:
        return create_test_subscription(user_id=user_id, doctor_id=doctor_id, plan_id=plan_id)

    # Plan-less: create minimal subscription for doctor-user connection
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    validity_days = 365
    end_date = (now + timedelta(days=validity_days)).isoformat()
    import_payment_id = f"IMPORT_{int(now.timestamp())}_{uuid.uuid4().hex[:8]}"

    subscription_item = {
        "subscription_id": str(uuid.uuid4()),
        "user_id": user_id,
        "doctor_id": doctor_id,
        "plan_id": "DOCTOR_IMPORT",
        "plan_price": Decimal("0"),
        "appointments_total": 999,
        "appointments_used": 0,
        "status": SubscriptionStatus.ACTIVE.value,
        "start_date": now_iso,
        "end_date": end_date,
        "payment_id": import_payment_id,
        "created_at": now_iso
    }

    try:
        USER_DOCTOR_SUBSCRIPTIONS_TABLE.put_item(
            Item=subscription_item,
            ConditionExpression="attribute_not_exists(subscription_id)"
        )
        logger.info(f"Created import subscription for user {user_id} to doctor {doctor_id}")
        sync_relation_for_subscription(user_id, doctor_id, subscription_item["subscription_id"])
        return subscription_item
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "ConditionalCheckFailedException":
            raise HTTPException(500, "Failed to create import subscription: duplicate")
        logger.error(f"DynamoDB error creating import subscription: {e}")
        raise HTTPException(500, "Failed to create import subscription")


def get_user_subscription(subscription_id: str) -> Optional[dict]:
    """
    Get a subscription by subscription_id.
    """
    try:
        response = USER_DOCTOR_SUBSCRIPTIONS_TABLE.get_item(Key={"subscription_id": subscription_id})
        return response.get("Item")
    except ClientError as e:
        logger.error(f"DynamoDB error fetching subscription: {e}")
        raise HTTPException(500, "Failed to fetch subscription")


def get_user_subscriptions(user_id: str) -> List[dict]:
    """
    Get all subscriptions for a user using GSI.
    """
    try:
        response = USER_DOCTOR_SUBSCRIPTIONS_TABLE.query(
            IndexName="GSI_UserSubscriptions",
            KeyConditionExpression=Key("user_id").eq(user_id)
        )
        subscriptions = response.get("Items", [])
        
        # Update status based on current state
        now = datetime.now(timezone.utc)
        for sub in subscriptions:
            sub = _update_subscription_status(sub, now)
        
        return subscriptions
    except ClientError as e:
        logger.error(f"DynamoDB error fetching user subscriptions: {e}")
        raise HTTPException(500, "Failed to fetch user subscriptions")


def get_doctor_subscribers(doctor_id: str) -> List[dict]:
    """
    Get all active subscribers for a doctor using GSI.
    """
    try:
        response = USER_DOCTOR_SUBSCRIPTIONS_TABLE.query(
            IndexName="GSI_DoctorSubscribers",
            KeyConditionExpression=Key("doctor_id").eq(doctor_id)
        )
        subscriptions = response.get("Items", [])
        
        # Update status based on current state
        now = datetime.now(timezone.utc)
        for sub in subscriptions:
            sub = _update_subscription_status(sub, now)
        
        return subscriptions
    except ClientError as e:
        logger.error(f"DynamoDB error fetching doctor subscribers: {e}")
        raise HTTPException(500, "Failed to fetch doctor subscribers")


def get_active_subscription_for_user_doctor(user_id: str, doctor_id: str) -> Optional[dict]:
    """
    Get the active subscription for a user-doctor pair.
    Returns the most recent active subscription.
    """
    try:
        # Query by user_id first (more selective)
        response = USER_DOCTOR_SUBSCRIPTIONS_TABLE.query(
            IndexName="GSI_UserSubscriptions",
            KeyConditionExpression=Key("user_id").eq(user_id)
        )
        
        subscriptions = response.get("Items", [])
        now = datetime.now(timezone.utc)
        
        # Filter by doctor_id and find active subscription
        active_subscription = None
        for sub in subscriptions:
            if sub.get("doctor_id") != doctor_id:
                continue
            
            sub = _update_subscription_status(sub, now)
            
            if sub.get("status") == SubscriptionStatus.ACTIVE.value:
                # If multiple active subscriptions, get the most recent one
                if not active_subscription:
                    active_subscription = sub
                else:
                    sub_created = datetime.fromisoformat(sub.get("created_at", "").replace('Z', '+00:00'))
                    active_created = datetime.fromisoformat(active_subscription.get("created_at", "").replace('Z', '+00:00'))
                    if sub_created > active_created:
                        active_subscription = sub
        
        return active_subscription
        
    except ClientError as e:
        logger.error(f"DynamoDB error fetching active subscription: {e}")
        raise HTTPException(500, "Failed to fetch active subscription")


def validate_subscription_for_booking(user_id: str, doctor_id: str) -> dict:
    """
    Validate if user has an active subscription for booking.
    Validates all three conditions:
    1. status == ACTIVE
    2. appointments_used < appointments_total
    3. current_time < end_date
    
    Returns validation result with subscription details.
    """
    subscription = get_active_subscription_for_user_doctor(user_id, doctor_id)
    
    if not subscription:
        return {
            "is_valid": False,
            "subscription_id": None,
            "appointments_remaining": None,
            "status": None,
            "message": "No active subscription found"
        }
    
    now = datetime.now(timezone.utc)
    subscription = _update_subscription_status(subscription, now)
    
    # Validate all three conditions explicitly
    status = subscription.get("status")
    appointments_used = int(subscription.get("appointments_used", 0))
    appointments_total = int(subscription.get("appointments_total", 0))
    appointments_remaining = appointments_total - appointments_used
    end_date_str = subscription.get("end_date")
    
    # Condition 1: status == ACTIVE
    if status != SubscriptionStatus.ACTIVE.value:
        return {
            "is_valid": False,
            "subscription_id": subscription.get("subscription_id"),
            "appointments_remaining": appointments_remaining,
            "status": status,
            "message": f"Subscription is {status.lower()}"
        }
    
    # Condition 2: appointments_used < appointments_total
    if appointments_used >= appointments_total:
        return {
            "is_valid": False,
            "subscription_id": subscription.get("subscription_id"),
            "appointments_remaining": 0,
            "status": status,
            "message": "No appointments remaining in subscription"
        }
    
    # Condition 3: current_time < end_date
    if end_date_str:
        try:
            end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            if now >= end_date:
                return {
                    "is_valid": False,
                    "subscription_id": subscription.get("subscription_id"),
                    "appointments_remaining": appointments_remaining,
                    "status": SubscriptionStatus.EXPIRED.value,
                    "message": "Subscription has expired"
                }
        except ValueError:
            logger.warning(f"Invalid end_date in subscription {subscription.get('subscription_id')}")
    
    return {
        "is_valid": True,
        "subscription_id": subscription.get("subscription_id"),
        "appointments_remaining": appointments_remaining,
        "status": status,
        "message": "Subscription is valid"
    }


def increment_appointments_used(subscription_id: str, user_id: str, doctor_id: str) -> dict:
    """
    Atomically increment appointments_used for a subscription.
    Also updates status to EXHAUSTED if appointments are used up.
    """
    # First, get the subscription to validate
    subscription = get_user_subscription(subscription_id)
    if not subscription:
        raise HTTPException(404, "Subscription not found")
    
    if subscription.get("user_id") != user_id or subscription.get("doctor_id") != doctor_id:
        raise HTTPException(403, "Subscription does not belong to this user-doctor pair")
    
    appointments_used = int(subscription.get("appointments_used", 0))
    appointments_total = int(subscription.get("appointments_total", 0))
    
    if appointments_used >= appointments_total:
        raise HTTPException(400, "All appointments have been used")
    
    now = datetime.now(timezone.utc)
    subscription = _update_subscription_status(subscription, now)
    
    # Validate all three conditions explicitly before incrementing
    # Condition 1: status == ACTIVE
    if subscription.get("status") != SubscriptionStatus.ACTIVE.value:
        raise HTTPException(400, f"Subscription is {subscription.get('status').lower()}")
    
    # Condition 2: appointments_used < appointments_total (already checked above)
    # Condition 3: current_time < end_date
    end_date_str = subscription.get("end_date")
    if end_date_str:
        try:
            end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            if now >= end_date:
                raise HTTPException(400, "Subscription has expired")
        except ValueError:
            logger.warning(f"Invalid end_date in subscription {subscription_id}")
    
    new_appointments_used = appointments_used + 1
    new_status = SubscriptionStatus.EXHAUSTED.value if new_appointments_used >= appointments_total else SubscriptionStatus.ACTIVE.value
    
    try:
        response = USER_DOCTOR_SUBSCRIPTIONS_TABLE.update_item(
            Key={"subscription_id": subscription_id},
            UpdateExpression="SET appointments_used = :used, #status = :status",
            ConditionExpression="appointments_used = :current_used AND #status = :active",
            ExpressionAttributeValues={
                ":used": new_appointments_used,
                ":status": new_status,
                ":current_used": appointments_used,
                ":active": SubscriptionStatus.ACTIVE.value
            },
            ExpressionAttributeNames={
                "#status": "status"
            },
            ReturnValues="ALL_NEW"
        )
        
        updated_subscription = response.get("Attributes", subscription)
        logger.info(f"Incremented appointments_used for subscription {subscription_id}: {new_appointments_used}/{appointments_total}")
        
        return updated_subscription
        
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            # Retry once if condition failed (concurrent update)
            logger.warning(f"Conditional check failed for subscription {subscription_id}, retrying...")
            return increment_appointments_used(subscription_id, user_id, doctor_id)
        logger.error(f"DynamoDB error incrementing appointments: {e}")
        raise HTTPException(500, "Failed to increment appointments used")


def cancel_subscription(subscription_id: str, user_id: str) -> dict:
    """
    Cancel a subscription.
    """
    subscription = get_user_subscription(subscription_id)
    if not subscription:
        raise HTTPException(404, "Subscription not found")
    
    if subscription.get("user_id") != user_id:
        raise HTTPException(403, "Subscription does not belong to this user")
    
    try:
        response = USER_DOCTOR_SUBSCRIPTIONS_TABLE.update_item(
            Key={"subscription_id": subscription_id},
            UpdateExpression="SET #status = :status",
            ExpressionAttributeValues={
                ":status": SubscriptionStatus.CANCELLED.value
            },
            ExpressionAttributeNames={
                "#status": "status"
            },
            ReturnValues="ALL_NEW"
        )
        
        logger.info(f"Cancelled subscription: {subscription_id}")
        return response.get("Attributes", subscription)
        
    except ClientError as e:
        logger.error(f"DynamoDB error cancelling subscription: {e}")
        raise HTTPException(500, "Failed to cancel subscription")


def _update_subscription_status(subscription: dict, now: datetime) -> dict:
    """
    Helper function to update subscription status based on current state.
    Checks expiration and exhaustion.
    """
    status = subscription.get("status")
    
    # Don't update if already cancelled or exhausted
    if status in [SubscriptionStatus.CANCELLED.value, SubscriptionStatus.EXHAUSTED.value]:
        return subscription
    
    # Check expiration
    end_date_str = subscription.get("end_date")
    if end_date_str:
        try:
            end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            if now > end_date:
                subscription["status"] = SubscriptionStatus.EXPIRED.value
                # Update in DB (async, don't wait)
                try:
                    USER_DOCTOR_SUBSCRIPTIONS_TABLE.update_item(
                        Key={"subscription_id": subscription.get("subscription_id")},
                        UpdateExpression="SET #status = :status",
                        ExpressionAttributeValues={
                            ":status": SubscriptionStatus.EXPIRED.value
                        },
                        ExpressionAttributeNames={
                            "#status": "status"
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to update expired status in DB: {e}")
                return subscription
        except ValueError:
            logger.warning(f"Invalid end_date in subscription {subscription.get('subscription_id')}")
    
    # Check exhaustion
    appointments_used = int(subscription.get("appointments_used", 0))
    appointments_total = int(subscription.get("appointments_total", 0))
    
    if appointments_used >= appointments_total and status == SubscriptionStatus.ACTIVE.value:
        subscription["status"] = SubscriptionStatus.EXHAUSTED.value
    
    return subscription
