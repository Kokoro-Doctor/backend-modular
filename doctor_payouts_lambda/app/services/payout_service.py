"""
Payout Service - manages doctor payout requests and processing
"""
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional, List
from fastapi import HTTPException
from botocore.exceptions import ClientError
from app.config import DOCTOR_PAYOUTS_TABLE, DOCTOR_EARNINGS_LEDGER_TABLE
from app.services.earnings_service import (
    get_available_earnings_for_month,
    mark_earnings_as_paid
)
from app.logger import get_logger
from boto3.dynamodb.conditions import Key

logger = get_logger(__name__)


def request_payout(
    doctor_id: str,
    payout_month: str,
    payout_method: str
) -> dict:
    """
    Request a payout for a specific month.
    Only one payout per doctor per month is allowed.
    Withdrawal is only allowed after month end.
    
    Args:
        doctor_id: Doctor ID
        payout_month: Month in YYYY-MM format
        payout_method: Payout method (BANK or UPI)
    
    Returns:
        dict: Created payout request
    """
    try:
        # Validate payout_month format
        try:
            datetime.strptime(payout_month, "%Y-%m")
        except ValueError:
            raise HTTPException(400, "Invalid payout_month format. Use YYYY-MM")
        
        # Validate that payout is requested for a past month
        now = datetime.now(timezone.utc)
        current_month = now.strftime("%Y-%m")
        
        # Allow payout only after month end (i.e., for previous months)
        if payout_month >= current_month:
            raise HTTPException(
                400,
                f"Payout can only be requested for completed months. "
                f"Current month: {current_month}, requested: {payout_month}"
            )
        
        # Check if payout already exists for this doctor and month
        existing_payout = get_payout_by_doctor_month(doctor_id, payout_month)
        if existing_payout:
            raise HTTPException(
                400,
                f"Payout already exists for doctor {doctor_id} and month {payout_month}. "
                f"Status: {existing_payout.get('status')}"
            )
        
        # Get available earnings for the month
        available_amount = get_available_earnings_for_month(doctor_id, payout_month)
        
        if available_amount <= 0:
            raise HTTPException(
                400,
                f"No available earnings for doctor {doctor_id} in month {payout_month}"
            )
        
        # Validate payout method
        if payout_method not in ["BANK", "UPI"]:
            raise HTTPException(400, "Invalid payout_method. Must be BANK or UPI")
        
        # Create payout request
        # Note: PK = doctor_id, SK = payout_month (no need to duplicate as separate attributes)
        payout_id = str(uuid.uuid4())
        payout_entry = {
            "PK": doctor_id,  # Partition key (doctor_id)
            "SK": payout_month,  # Sort key (YYYY-MM)
            "payout_id": payout_id,
            "total_amount": float(available_amount),
            "status": "REQUESTED",
            "payout_method": payout_method,
            "transaction_reference": None,
            "requested_at": now.isoformat(),
            "processed_at": None
        }
        
        DOCTOR_PAYOUTS_TABLE.put_item(
            Item=payout_entry,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)"
        )
        
        logger.info(
            f"Created payout request: {payout_id} for doctor {doctor_id}, "
            f"month {payout_month}, amount: {available_amount}"
        )
        
        # Add doctor_id and payout_month for API response (mapped from PK/SK)
        payout_entry["doctor_id"] = payout_entry["PK"]
        payout_entry["payout_month"] = payout_entry["SK"]
        return payout_entry
        
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "ConditionalCheckFailedException":
            raise HTTPException(400, "Payout already exists for this doctor and month")
        logger.error(f"DynamoDB error creating payout: {e}")
        raise HTTPException(500, "Failed to create payout request")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating payout request: {str(e)}", exc_info=True)
        raise HTTPException(500, f"Failed to create payout request: {str(e)}")


def get_payout_by_doctor_month(doctor_id: str, payout_month: str) -> Optional[dict]:
    """
    Get payout by doctor_id and payout_month.
    """
    try:
        response = DOCTOR_PAYOUTS_TABLE.get_item(
            Key={"PK": doctor_id, "SK": payout_month}
        )
        item = response.get("Item")
        if item:
            # Map PK/SK to doctor_id/payout_month for API response
            item["doctor_id"] = item.get("PK")
            item["payout_month"] = item.get("SK")
        return item
    except ClientError as e:
        logger.error(f"DynamoDB error fetching payout: {e}")
        return None


def get_payout_by_id(payout_id: str) -> Optional[dict]:
    """
    Get payout by payout_id using GSI.
    """
    try:
        response = DOCTOR_PAYOUTS_TABLE.query(
            IndexName="GSI_PayoutId",
            KeyConditionExpression=Key("payout_id").eq(payout_id)
        )
        items = response.get("Items", [])
        if items:
            item = items[0]
            # Map PK/SK to doctor_id/payout_month for API response
            item["doctor_id"] = item.get("PK")
            item["payout_month"] = item.get("SK")
            return item
        return None
    except ClientError as e:
        logger.error(f"DynamoDB error fetching payout by ID: {e}")
        return None


def get_doctor_payouts(doctor_id: str) -> List[dict]:
    """
    Get all payouts for a doctor.
    """
    try:
        response = DOCTOR_PAYOUTS_TABLE.query(
            KeyConditionExpression=Key("PK").eq(doctor_id)
        )
        items = response.get("Items", [])
        # Map PK/SK to doctor_id/payout_month for API response
        for item in items:
            item["doctor_id"] = item.get("PK")
            item["payout_month"] = item.get("SK")
        return items
    except ClientError as e:
        logger.error(f"DynamoDB error fetching doctor payouts: {e}")
        raise HTTPException(500, "Failed to fetch doctor payouts")


def update_payout_status(
    payout_id: str,
    status: str,
    transaction_reference: Optional[str] = None
) -> dict:
    """
    Update payout status (admin function).
    
    Args:
        payout_id: Payout ID
        status: New status (PROCESSING, COMPLETED, FAILED)
        transaction_reference: Optional transaction reference
    
    Returns:
        dict: Updated payout entry
    """
    try:
        # Get payout
        payout = get_payout_by_id(payout_id)
        if not payout:
            raise HTTPException(404, "Payout not found")
        
        # Use PK/SK instead of redundant attributes
        doctor_id = payout.get("PK")
        payout_month = payout.get("SK")
        
        # Validate status transition
        current_status = payout.get("status")
        valid_transitions = {
            "REQUESTED": ["PROCESSING", "FAILED"],
            "PROCESSING": ["COMPLETED", "FAILED"],
            "COMPLETED": [],
            "FAILED": ["REQUESTED", "PROCESSING"]
        }
        
        if status not in valid_transitions.get(current_status, []):
            raise HTTPException(
                400,
                f"Invalid status transition from {current_status} to {status}"
            )
        
        # Update payout
        update_expr_parts = ["#status = :status", "processed_at = :processed_at"]
        expr_attr_values = {
            ":status": status,
            ":processed_at": datetime.now(timezone.utc).isoformat()
        }
        expr_attr_names = {"#status": "status"}
        
        if transaction_reference:
            update_expr_parts.append("transaction_reference = :ref")
            expr_attr_values[":ref"] = transaction_reference
        
        DOCTOR_PAYOUTS_TABLE.update_item(
            Key={"PK": doctor_id, "SK": payout_month},
            UpdateExpression="SET " + ", ".join(update_expr_parts),
            ExpressionAttributeValues=expr_attr_values,
            ExpressionAttributeNames=expr_attr_names,
            ReturnValues="ALL_NEW"
        )
        
        updated_payout = get_payout_by_id(payout_id)
        # Note: get_payout_by_id already maps PK/SK to doctor_id/payout_month
        
        # If status is COMPLETED, mark earnings as PAID
        if status == "COMPLETED":
            # Get all payment_ids for this month's earnings
            response = DOCTOR_EARNINGS_LEDGER_TABLE.query(
                KeyConditionExpression=Key("PK").eq(doctor_id) & Key("SK").begins_with(f"{payout_month}#")
            )
            payment_ids = [item.get("payment_id") for item in response.get("Items", [])]
            
            if payment_ids:
                mark_earnings_as_paid(doctor_id, payout_month, payment_ids)
                logger.info(f"Marked earnings as PAID for doctor {doctor_id}, month {payout_month}")
        
        logger.info(f"Updated payout {payout_id} status to {status}")
        return updated_payout
        
    except HTTPException:
        raise
    except ClientError as e:
        logger.error(f"DynamoDB error updating payout: {e}")
        raise HTTPException(500, "Failed to update payout status")
    except Exception as e:
        logger.error(f"Error updating payout status: {str(e)}", exc_info=True)
        raise HTTPException(500, f"Failed to update payout status: {str(e)}")


def get_all_pending_payouts() -> List[dict]:
    """
    Get all payouts with status REQUESTED or PROCESSING (admin function).
    """
    try:
        # Scan table and filter by status
        # Note: In production, consider using GSI on status for better performance
        response = DOCTOR_PAYOUTS_TABLE.scan()
        all_payouts = response.get("Items", [])
        
        pending_payouts = [
            payout for payout in all_payouts
            if payout.get("status") in ["REQUESTED", "PROCESSING"]
        ]
        
        # Map PK/SK to doctor_id/payout_month for API response
        for payout in pending_payouts:
            payout["doctor_id"] = payout.get("PK")
            payout["payout_month"] = payout.get("SK")
        
        return pending_payouts
        
    except ClientError as e:
        logger.error(f"DynamoDB error fetching pending payouts: {e}")
        raise HTTPException(500, "Failed to fetch pending payouts")

