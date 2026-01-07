"""
Earnings Service - manages doctor earnings ledger entries
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, List
from fastapi import HTTPException
from botocore.exceptions import ClientError
from app.config import DOCTOR_EARNINGS_LEDGER_TABLE
from app.logger import get_logger
from boto3.dynamodb.conditions import Key

logger = get_logger(__name__)


def create_earnings_entry(
    doctor_id: str,
    user_id: str,
    subscription_id: str,
    payment_id: str,
    gross_amount: Decimal,
    platform_fee_percentage: float = 20.0
) -> dict:
    """
    Create an immutable earnings ledger entry after successful payment.
    
    Args:
        doctor_id: Doctor who earned the money
        user_id: User who made the payment
        subscription_id: Subscription associated with the payment
        payment_id: Razorpay payment ID
        gross_amount: Total payment amount
        platform_fee_percentage: Platform fee percentage (default 20%)
    
    Returns:
        dict: Created earnings entry
    """
    try:
        # Calculate platform fee and net amount
        platform_fee = Decimal(str(round(float(gross_amount) * (platform_fee_percentage / 100), 2)))
        net_amount = gross_amount - platform_fee
        
        # Generate earning_id: YYYY-MM#payment_id
        now = datetime.now(timezone.utc)
        earning_month = now.strftime("%Y-%m")
        earning_id = f"{earning_month}#{payment_id}"
        
        # Create earnings entry
        # Note: PK = doctor_id, SK = earning_id (no need to duplicate as separate attributes)
        earnings_entry = {
            "PK": doctor_id,  # Partition key (doctor_id)
            "SK": earning_id,  # Sort key (YYYY-MM#payment_id)
            "user_id": user_id,
            "subscription_id": subscription_id,
            "payment_id": payment_id,
            "gross_amount": gross_amount,
            "platform_fee": platform_fee,
            "net_amount": net_amount,
            "earning_month": earning_month,
            "status": "AVAILABLE",
            "created_at": now.isoformat()
        }
        
        # Use conditional write to ensure idempotency
        DOCTOR_EARNINGS_LEDGER_TABLE.put_item(
            Item=earnings_entry,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)"
        )
        
        logger.info(
            f"Created earnings entry: {earning_id} for doctor {doctor_id}, "
            f"gross: {gross_amount}, net: {net_amount}, platform_fee: {platform_fee}"
        )
        
        return earnings_entry
        
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "ConditionalCheckFailedException":
            # Entry already exists, fetch and return it
            logger.warning(f"Earnings entry already exists for payment_id {payment_id}, fetching existing entry")
            existing_entry = get_earnings_entry_by_payment_id(payment_id)
            if existing_entry:
                return existing_entry
            raise HTTPException(500, "Failed to create earnings entry: race condition detected")
        logger.error(f"DynamoDB error creating earnings entry: {e}")
        raise HTTPException(500, "Failed to create earnings entry")
    except Exception as e:
        logger.error(f"Error creating earnings entry: {str(e)}", exc_info=True)
        raise HTTPException(500, f"Failed to create earnings entry: {str(e)}")


def get_earnings_entry_by_payment_id(payment_id: str) -> Optional[dict]:
    """
    Get earnings entry by payment_id using GSI.
    Returns the entry if found, None otherwise.
    """
    try:
        response = DOCTOR_EARNINGS_LEDGER_TABLE.query(
            IndexName="GSI_PaymentEarnings",
            KeyConditionExpression=Key("payment_id").eq(payment_id)
        )
        items = response.get("Items", [])
        if items:
            return items[0]
        return None
    except ClientError as e:
        logger.error(f"DynamoDB error fetching earnings by payment_id: {e}")
        return None


def get_doctor_earnings_summary(
    doctor_id: str,
    month: Optional[str] = None
) -> dict:
    """
    Get earnings summary for a doctor.
    If month is provided, returns summary for that month only.
    Otherwise returns total summary.
    
    Args:
        doctor_id: Doctor ID
        month: Optional month in YYYY-MM format
    
    Returns:
        dict: Earnings summary with total_gross, total_platform_fee, total_net, available_amount, paid_amount
    """
    try:
        if month:
            # Query specific month
            response = DOCTOR_EARNINGS_LEDGER_TABLE.query(
                KeyConditionExpression=Key("PK").eq(doctor_id) & Key("SK").begins_with(f"{month}#")
            )
        else:
            # Query all earnings for doctor
            response = DOCTOR_EARNINGS_LEDGER_TABLE.query(
                KeyConditionExpression=Key("PK").eq(doctor_id)
            )
        
        items = response.get("Items", [])
        
        total_gross = Decimal("0")
        total_platform_fee = Decimal("0")
        total_net = Decimal("0")
        available_amount = Decimal("0")
        paid_amount = Decimal("0")
        
        for item in items:
            total_gross += Decimal(str(item.get("gross_amount", 0)))
            total_platform_fee += Decimal(str(item.get("platform_fee", 0)))
            total_net += Decimal(str(item.get("net_amount", 0)))
            
            status = item.get("status", "AVAILABLE")
            if status == "AVAILABLE":
                available_amount += Decimal(str(item.get("net_amount", 0)))
            elif status == "PAID":
                paid_amount += Decimal(str(item.get("net_amount", 0)))
        
        return {
            "doctor_id": doctor_id,
            "month": month,
            "total_gross": float(total_gross),
            "total_platform_fee": float(total_platform_fee),
            "total_net": float(total_net),
            "available_amount": float(available_amount),
            "paid_amount": float(paid_amount),
            "entry_count": len(items)
        }
        
    except ClientError as e:
        logger.error(f"DynamoDB error fetching earnings summary: {e}")
        raise HTTPException(500, "Failed to fetch earnings summary")


def get_available_earnings_for_month(doctor_id: str, month: str) -> Decimal:
    """
    Get total available earnings for a specific month.
    Used for payout validation.
    
    Args:
        doctor_id: Doctor ID
        month: Month in YYYY-MM format
    
    Returns:
        Decimal: Total available amount for the month
    """
    try:
        response = DOCTOR_EARNINGS_LEDGER_TABLE.query(
            KeyConditionExpression=Key("PK").eq(doctor_id) & Key("SK").begins_with(f"{month}#")
        )
        
        items = response.get("Items", [])
        total_available = Decimal("0")
        
        for item in items:
            if item.get("status") == "AVAILABLE":
                total_available += Decimal(str(item.get("net_amount", 0)))
        
        return total_available
        
    except ClientError as e:
        logger.error(f"DynamoDB error fetching available earnings: {e}")
        raise HTTPException(500, "Failed to fetch available earnings")


def mark_earnings_as_paid(doctor_id: str, month: str, payment_ids: List[str]) -> int:
    """
    Mark earnings entries as PAID after successful payout.
    
    Args:
        doctor_id: Doctor ID
        month: Month in YYYY-MM format
        payment_ids: List of payment_ids to mark as paid
    
    Returns:
        int: Number of entries marked as paid
    """
    try:
        # Query all earnings for the month
        response = DOCTOR_EARNINGS_LEDGER_TABLE.query(
            KeyConditionExpression=Key("PK").eq(doctor_id) & Key("SK").begins_with(f"{month}#")
        )
        
        items = response.get("Items", [])
        updated_count = 0
        
        for item in items:
            payment_id = item.get("payment_id")
            if payment_id in payment_ids and item.get("status") == "AVAILABLE":
                # Update status to PAID
                DOCTOR_EARNINGS_LEDGER_TABLE.update_item(
                    Key={"PK": doctor_id, "SK": item.get("SK")},
                    UpdateExpression="SET #status = :status",
                    ExpressionAttributeValues={":status": "PAID"},
                    ExpressionAttributeNames={"#status": "status"}
                )
                updated_count += 1
        
        logger.info(f"Marked {updated_count} earnings entries as PAID for doctor {doctor_id}, month {month}")
        return updated_count
        
    except ClientError as e:
        logger.error(f"DynamoDB error marking earnings as paid: {e}")
        raise HTTPException(500, "Failed to mark earnings as paid")

