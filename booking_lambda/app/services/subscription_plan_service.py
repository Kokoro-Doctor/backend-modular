"""
Subscription Plan Service - manages subscription plan definitions

IMPORTANT: plan_id is ONLY an identifier. The database table is ALWAYS the source of truth.
Never derive price, validity, or any plan attributes from the plan_id itself.
The plan_id format (PLAN_<PRICE>_<DURATION>D_<SCOPE>) is for human readability only.
"""
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import HTTPException
from botocore.exceptions import ClientError
from app.config import SUBSCRIPTION_PLANS_TABLE
from app.models.schemas import SubscriptionPlanCreate, SubscriptionPlanUpdate
from app.utils.plan_id_utils import generate_plan_id, validate_plan_id_format, validate_plan_id_matches_data
from app.logger import get_logger

logger = get_logger(__name__)


def create_subscription_plan(plan_data: SubscriptionPlanCreate, plan_id: Optional[str] = None) -> dict:
    """
    Create a new subscription plan with a human-readable plan_id.
    Plans are immutable once created (for historical tracking).
    
    Args:
        plan_data: Plan data including price, validity_days, doctor_id, etc.
        plan_id: Optional plan_id. If not provided, generates one using format:
                 PLAN_<PRICE>_<DURATION>D_<SCOPE>
    
    Returns:
        Created plan item with plan_id
    
    IMPORTANT: plan_id is ONLY an identifier. The database table is ALWAYS the source of truth.
    """
    # Generate plan_id if not provided
    if plan_id:
        # Validate format if provided
        if not validate_plan_id_format(plan_id):
            raise HTTPException(400, f"Invalid plan_id format: {plan_id}. Expected format: PLAN_<PRICE>_<DURATION>D_<SCOPE>")
        
        # Validate that plan_id matches the plan data
        if not validate_plan_id_matches_data(plan_id, plan_data.price, plan_data.validity_days, plan_data.doctor_id):
            raise HTTPException(
                400,
                "plan_id does not match plan data. plan_id format encodes price, validity_days, and doctor_id. "
                "Either provide a matching plan_id or omit it to auto-generate."
            )
    else:
        # Auto-generate human-readable plan_id
        try:
            plan_id = generate_plan_id(
                price=plan_data.price,
                validity_days=plan_data.validity_days,
                doctor_id=plan_data.doctor_id
            )
        except ValueError as e:
            raise HTTPException(400, f"Failed to generate plan_id: {str(e)}")
    
    # Check if plan_id already exists
    existing_plan = get_subscription_plan(plan_id)
    if existing_plan:
        raise HTTPException(409, f"Plan with plan_id '{plan_id}' already exists")
    
    now_iso = datetime.now(timezone.utc).isoformat()
    
    plan_item = {
        "plan_id": plan_id,
        "doctor_id": plan_data.doctor_id,
        "price": plan_data.price,
        "appointments_allowed": plan_data.appointments_allowed,
        "validity_days": plan_data.validity_days,
        "valid_from": plan_data.valid_from,
        "valid_to": plan_data.valid_to,
        "is_active": plan_data.is_active,
        "created_at": now_iso
    }
    
    try:
        SUBSCRIPTION_PLANS_TABLE.put_item(Item=plan_item)
        logger.info(f"Created subscription plan: {plan_id} (price: {plan_data.price}, validity: {plan_data.validity_days}D, scope: {plan_data.doctor_id})")
        return plan_item
    except ClientError as e:
        logger.error(f"DynamoDB error creating plan: {e}")
        raise HTTPException(500, "Failed to create subscription plan")


def get_subscription_plan(plan_id: str) -> Optional[dict]:
    """
    Get a subscription plan by plan_id.
    
    IMPORTANT: Always fetches plan data from database. Never derives plan attributes
    from plan_id format. The database is the source of truth.
    """
    try:
        response = SUBSCRIPTION_PLANS_TABLE.get_item(Key={"plan_id": plan_id})
        return response.get("Item")
    except ClientError as e:
        logger.error(f"DynamoDB error fetching plan: {e}")
        raise HTTPException(500, "Failed to fetch subscription plan")


def get_active_plans_for_doctor(doctor_id: str) -> List[dict]:
    """
    Get all active plans for a specific doctor.
    Returns both doctor-specific plans and global plans (doctor_id='ALL').
    """
    try:
        # Scan for active plans (since we need to check doctor_id='ALL' as well)
        # In production, consider adding a GSI on doctor_id + is_active
        response = SUBSCRIPTION_PLANS_TABLE.scan(
            FilterExpression="is_active = :active",
            ExpressionAttributeValues={":active": True}
        )
        
        plans = response.get("Items", [])
        now = datetime.now(timezone.utc)
        
        # Filter plans that are valid for this doctor and within date range
        valid_plans = []
        for plan in plans:
            plan_doctor_id = plan.get("doctor_id")
            
            # Check if plan is for this doctor or global
            if plan_doctor_id != "ALL" and plan_doctor_id != doctor_id:
                continue
            
            # Check date validity
            valid_from_str = plan.get("valid_from")
            valid_to_str = plan.get("valid_to")
            
            if valid_from_str:
                try:
                    valid_from = datetime.fromisoformat(valid_from_str.replace('Z', '+00:00'))
                    if now < valid_from:
                        continue
                except ValueError:
                    logger.warning(f"Invalid valid_from date in plan {plan.get('plan_id')}")
            
            if valid_to_str:
                try:
                    valid_to = datetime.fromisoformat(valid_to_str.replace('Z', '+00:00'))
                    if now > valid_to:
                        continue
                except ValueError:
                    logger.warning(f"Invalid valid_to date in plan {plan.get('plan_id')}")
            
            valid_plans.append(plan)
        
        return valid_plans
        
    except ClientError as e:
        logger.error(f"DynamoDB error fetching plans: {e}")
        raise HTTPException(500, "Failed to fetch subscription plans")


def get_all_active_plans() -> List[dict]:
    """
    Get all active subscription plans (for admin/listing purposes).
    """
    try:
        response = SUBSCRIPTION_PLANS_TABLE.scan(
            FilterExpression="is_active = :active",
            ExpressionAttributeValues={":active": True}
        )
        return response.get("Items", [])
    except ClientError as e:
        logger.error(f"DynamoDB error fetching all plans: {e}")
        raise HTTPException(500, "Failed to fetch subscription plans")


def update_subscription_plan(plan_id: str, update_data: SubscriptionPlanUpdate) -> dict:
    """
    Update a subscription plan.
    Note: For immutability, consider creating a new plan version instead.
    This function allows updating active status and date ranges.
    """
    plan = get_subscription_plan(plan_id)
    if not plan:
        raise HTTPException(404, "Subscription plan not found")
    
    update_expression_parts = []
    expression_attribute_values = {}
    expression_attribute_names = {}
    
    if update_data.price is not None:
        update_expression_parts.append("#price = :price")
        expression_attribute_names["#price"] = "price"
        expression_attribute_values[":price"] = update_data.price
    
    if update_data.appointments_allowed is not None:
        update_expression_parts.append("#appointments_allowed = :appointments_allowed")
        expression_attribute_names["#appointments_allowed"] = "appointments_allowed"
        expression_attribute_values[":appointments_allowed"] = update_data.appointments_allowed
    
    if update_data.validity_days is not None:
        update_expression_parts.append("validity_days = :validity_days")
        expression_attribute_values[":validity_days"] = update_data.validity_days
    
    if update_data.valid_from is not None:
        update_expression_parts.append("valid_from = :valid_from")
        expression_attribute_values[":valid_from"] = update_data.valid_from
    
    if update_data.valid_to is not None:
        update_expression_parts.append("valid_to = :valid_to")
        expression_attribute_values[":valid_to"] = update_data.valid_to
    
    if update_data.is_active is not None:
        update_expression_parts.append("is_active = :is_active")
        expression_attribute_values[":is_active"] = update_data.is_active
    
    if not update_expression_parts:
        return plan
    
    update_expression = "SET " + ", ".join(update_expression_parts)
    
    try:
        response = SUBSCRIPTION_PLANS_TABLE.update_item(
            Key={"plan_id": plan_id},
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_attribute_values,
            ExpressionAttributeNames=expression_attribute_names if expression_attribute_names else None,
            ReturnValues="ALL_NEW"
        )
        logger.info(f"Updated subscription plan: {plan_id}")
        return response.get("Attributes", plan)
    except ClientError as e:
        logger.error(f"DynamoDB error updating plan: {e}")
        raise HTTPException(500, "Failed to update subscription plan")

