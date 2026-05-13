"""
Hospital service - handles hospital CRUD operations in DynamoDB.
"""
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

import bcrypt
from fastapi import HTTPException
from boto3.dynamodb.conditions import Attr, Key

from app.config import HOSPITALS_TABLE
from app.auth.jwt_auth import create_hospital_token
from app.logger import get_logger

logger = get_logger(__name__)


def generate_hospital_id() -> str:
    """Generate hospital_id in format HOSP_<uuid>."""
    return f"HOSP_{uuid.uuid4()}"


def _is_email(identifier: str) -> bool:
    return bool(re.match(r"[^@]+@[^@]+\.[^@]+", identifier))


def _public(hospital: dict) -> dict:
    """Return a copy of the hospital record with sensitive fields removed."""
    return {k: v for k, v in hospital.items() if k not in ("password_hash",)}


def _find_by_email(email: str) -> Optional[dict]:
    """Query email-index GSI for a hospital with the given email."""
    resp = HOSPITALS_TABLE.query(
        IndexName="email-index",
        KeyConditionExpression=Key("email").eq(email),
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def _find_by_contact(contact_number: str) -> Optional[dict]:
    """Query contact_number-index GSI for a hospital with the given contact number."""
    resp = HOSPITALS_TABLE.query(
        IndexName="contact_number-index",
        KeyConditionExpression=Key("contact_number").eq(contact_number),
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def create_hospital(data: dict) -> dict:
    """Create a new hospital in DynamoDB with bcrypt-hashed password."""
    email = (data.get("email") or "").strip() or None
    contact_number = (data.get("contact_number") or "").strip() or None

    if email and _find_by_email(email):
        raise HTTPException(status_code=409, detail="A hospital with this email already exists")
    if contact_number and _find_by_contact(contact_number):
        raise HTTPException(status_code=409, detail="A hospital with this contact number already exists")

    hospital_id = generate_hospital_id()
    password_hash = bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt()).decode()
    now_iso = datetime.now(timezone.utc).isoformat()

    item = {
        "hospital_id": hospital_id,
        "name": data.get("name", ""),
        "password_hash": password_hash,
        "created_at": now_iso,
        "is_active": True,
    }
    # Only store optional fields when present so GSI doesn't index empty strings
    if email:
        item["email"] = email
    if contact_number:
        item["contact_number"] = contact_number
    if data.get("address"):
        item["address"] = data["address"]
    if data.get("city"):
        item["city"] = data["city"]
    if data.get("state"):
        item["state"] = data["state"]

    HOSPITALS_TABLE.put_item(Item=item)
    logger.info(f"[create_hospital] Created hospital {hospital_id}")
    return _public(item)


def list_hospitals(active_only: bool = True) -> list:
    """List all hospitals. By default returns only active ones."""
    try:
        if active_only:
            response = HOSPITALS_TABLE.scan(
                FilterExpression=Attr("is_active").eq(True)
            )
        else:
            response = HOSPITALS_TABLE.scan()
        return [_public(h) for h in response.get("Items", [])]
    except Exception as e:
        logger.error(f"[list_hospitals] Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to list hospitals")


def get_hospital(hospital_id: str, include_private: bool = False) -> Optional[dict]:
    """Get hospital by hospital_id. Strips password_hash unless include_private=True."""
    try:
        response = HOSPITALS_TABLE.get_item(Key={"hospital_id": hospital_id})
        item = response.get("Item")
        if item is None:
            return None
        return item if include_private else _public(item)
    except Exception as e:
        logger.error(f"[get_hospital] Error fetching {hospital_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch hospital")


def get_hospital_or_raise(hospital_id: str, include_private: bool = False) -> dict:
    """Get hospital by ID or raise 404."""
    hospital = get_hospital(hospital_id, include_private=include_private)
    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return hospital


def update_hospital(hospital_id: str, data: dict) -> dict:
    """Update hospital attributes."""
    hospital = get_hospital_or_raise(hospital_id)

    update_expr_parts = []
    expr_values = {}

    for key in ["name", "address", "city", "state", "contact_number", "email"]:
        if key in data and data[key] is not None:
            placeholder = f":{key}"
            update_expr_parts.append(f"{key} = {placeholder}")
            expr_values[placeholder] = data[key]

    if not update_expr_parts:
        return hospital

    update_expr = "SET " + ", ".join(update_expr_parts)
    HOSPITALS_TABLE.update_item(
        Key={"hospital_id": hospital_id},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_values,
    )

    updated = get_hospital_or_raise(hospital_id)
    logger.info(f"[update_hospital] Updated hospital {hospital_id}")
    return updated


def disable_hospital(hospital_id: str) -> dict:
    """Soft delete: set is_active = false."""
    get_hospital_or_raise(hospital_id)
    HOSPITALS_TABLE.update_item(
        Key={"hospital_id": hospital_id},
        UpdateExpression="SET is_active = :val",
        ExpressionAttributeValues={":val": False},
    )
    updated = get_hospital_or_raise(hospital_id)
    logger.info(f"[disable_hospital] Disabled hospital {hospital_id}")
    return updated


def validate_hospital_login(identifier: str, password: str) -> tuple[dict, str]:
    """
    Authenticate a hospital by email or contact_number + password.
    Returns (hospital_public, jwt_token) on success.
    Raises HTTPException on invalid credentials or disabled account.
    """
    identifier = identifier.strip()

    if _is_email(identifier):
        hospital = _find_by_email(identifier)
        lookup_desc = f"email={identifier!r}"
    else:
        hospital = _find_by_contact(identifier)
        lookup_desc = f"contact_number={identifier!r}"

    if not hospital:
        logger.warning(f"[validate_hospital_login] No hospital found for {lookup_desc}")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    stored_hash = hospital.get("password_hash", "")
    if not stored_hash:
        logger.error(f"[validate_hospital_login] No password_hash for hospital_id={hospital.get('hospital_id')!r}")
        raise HTTPException(status_code=503, detail="Hospital credentials not configured")

    if not bcrypt.checkpw(password.encode(), stored_hash.encode()):
        logger.warning(f"[validate_hospital_login] Wrong password for {lookup_desc}")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not hospital.get("is_active", True):
        raise HTTPException(status_code=403, detail="Hospital account is disabled")

    hospital_id = hospital["hospital_id"]
    token = create_hospital_token(hospital_id)
    logger.info(f"[validate_hospital_login] Login success hospital_id={hospital_id!r}")
    return _public(hospital), token
