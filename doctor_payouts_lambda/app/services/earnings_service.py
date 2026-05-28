"""
Earnings Service - manages doctor earnings ledger entries
(Shared service, also used by payment lambda)
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

