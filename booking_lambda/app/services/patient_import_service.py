"""
Patient Import Service - Excel upload, user lookup/create, subscription creation.
"""
import io
from typing import Optional
from fastapi import HTTPException, UploadFile
import pandas as pd

from app.config import USERS_TABLE
from app.utils.db_utils import normalize_phone_number, generate_user_id
from app.services.user_subscription_service import (
    get_active_subscription_for_user_doctor,
    create_import_subscription,
)
from app.services.user_doctor_relation_service import (
    create_relation,
    RelationType,
    LinkedBy,
)
from app.logger import get_logger
from boto3.dynamodb.conditions import Key
from datetime import datetime, timezone

logger = get_logger(__name__)

# Excel column names (case-insensitive)
COLUMNS = ["name", "phone", "email", "age", "gender", "condition"]


def _get_user_by_phone(phone: str) -> Optional[dict]:
    """Get user by phone number using GSI."""
    try:
        normalized = normalize_phone_number(phone)
        if not normalized:
            return None
        response = USERS_TABLE.query(
            IndexName="phone-index",
            KeyConditionExpression=Key("phoneNumber").eq(normalized)
        )
        items = response.get("Items", [])
        return items[0] if items else None
    except Exception as e:
        logger.error(f"[get_user_by_phone] Error: {e}")
        return None


def _create_user(phone: str, name: Optional[str] = None, email: Optional[str] = None) -> dict:
    """Create minimal user record for doctor import."""
    normalized = normalize_phone_number(phone)
    if not normalized:
        raise ValueError(f"Invalid phone number: {phone}")

    user_id = generate_user_id()
    now_iso = datetime.now(timezone.utc).isoformat()

    user_item = {
        "user_id": user_id,
        "phoneNumber": normalized,
        "createdAt": now_iso,
        "source": "doctor_import",
    }
    if name and str(name).strip():
        user_item["name"] = str(name).strip()
    if email and str(email).strip():
        user_item["email"] = str(email).strip().lower()

    try:
        USERS_TABLE.put_item(Item=user_item)
        logger.info(f"[create_user] Created user {user_id} (phone: {normalized})")
        return user_item
    except Exception as e:
        logger.error(f"[create_user] Error: {e}")
        raise HTTPException(500, f"Failed to create user: {e}")


def _normalize_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names to lowercase for case-insensitive matching."""
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def process_patient_excel(
    file: UploadFile,
    doctor_id: str,
    plan_id: Optional[str] = None,
) -> dict:
    """
    Process Excel file: parse rows, get/create users by phone, subscribe to doctor.
    Skips rows without phone. Skips rows where user already subscribed.
    Returns summary: total_rows, users_created, existing_users, subscriptions_created, skipped.
    """
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "File must be an Excel file (.xlsx or .xls)")

    # Read file into memory first - avoids Lambda/API Gateway truncation issues
    # (multipart binary can be corrupted when passed as stream)
    try:
        content = file.file.read()
        if not content:
            raise HTTPException(400, "Uploaded file is empty")
    except Exception as e:
        logger.error(f"[process_patient_excel] Failed to read file: {e}")
        raise HTTPException(400, f"Failed to read file: {str(e)}")

    try:
        df = pd.read_excel(io.BytesIO(content), engine="openpyxl")
    except Exception as e:
        logger.error(f"[process_patient_excel] Failed to parse Excel: {e}")
        raise HTTPException(400, f"Invalid Excel file. Ensure it is a valid .xlsx file: {str(e)}")

    df = _normalize_column_names(df)

    if "phone" not in df.columns:
        raise HTTPException(400, "Excel must contain a 'phone' column")

    rows = df.to_dict("records")
    total_rows = len(rows)
    users_created = 0
    existing_users = 0
    subscriptions_created = 0
    skipped = 0

    for row in rows:
        phone_raw = row.get("phone")
        if phone_raw is None or (isinstance(phone_raw, float) and pd.isna(phone_raw)):
            skipped += 1
            continue

        phone = str(phone_raw).strip()
        if not phone:
            skipped += 1
            continue

        normalized_phone = normalize_phone_number(phone)
        if not normalized_phone:
            logger.warning(f"[process_patient_excel] Skipping invalid phone: {phone}")
            skipped += 1
            continue

        # Get or create user
        user = _get_user_by_phone(phone)
        if user:
            user_id = user.get("user_id")
            existing_users += 1
        else:
            name = row.get("name")
            email = row.get("email")
            if name is not None and isinstance(name, float) and pd.isna(name):
                name = None
            if email is not None and isinstance(email, float) and pd.isna(email):
                email = None
            try:
                user = _create_user(phone=phone, name=name, email=email)
                user_id = user.get("user_id")
                users_created += 1
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"[process_patient_excel] Failed to create user for {phone}: {e}")
                skipped += 1
                continue

        # Ensure relation exists regardless of subscription state
        try:
            create_relation(
                user_id=user_id,
                doctor_id=doctor_id,
                relation_type=RelationType.SUBSCRIPTION,
                linked_by=LinkedBy.DOCTOR,
            )
        except Exception as e:
            logger.warning(f"[process_patient_excel] Relation creation failed for {user_id}: {e}")

        # Check if already subscribed
        existing_sub = get_active_subscription_for_user_doctor(user_id, doctor_id)
        if existing_sub:
            skipped += 1
            continue

        # Create subscription (also syncs relation internally)
        try:
            create_import_subscription(user_id=user_id, doctor_id=doctor_id, plan_id=plan_id)
            subscriptions_created += 1
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[process_patient_excel] Failed to create subscription for {user_id}: {e}")
            skipped += 1

    return {
        "total_rows": total_rows,
        "users_created": users_created,
        "existing_users": existing_users,
        "subscriptions_created": subscriptions_created,
        "skipped": skipped,
    }
