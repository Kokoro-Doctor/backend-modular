"""
Earnings Summary Service - provides earnings summaries for doctors
"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from fastapi import HTTPException
from botocore.exceptions import ClientError
from app.config import DOCTOR_EARNINGS_LEDGER_TABLE
from app.logger import get_logger
from boto3.dynamodb.conditions import Key

logger = get_logger(__name__)


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

