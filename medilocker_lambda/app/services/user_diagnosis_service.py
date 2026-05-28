"""
Persist diagnosis fields extracted by insurance autofill onto the Users table.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from botocore.exceptions import ClientError

from app.config import users_table
from app.logger import get_logger

logger = get_logger(__name__)

DIAGNOSIS_FIELDS = (
    "primary_diagnosis",
    "primary_icd_code",
    "additional_diagnosis",
    "additional_icd_code",
)


def _clean_value(value: Any) -> Optional[Any]:
    """Return a DynamoDB-safe value only when it is present and non-empty."""
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, (list, tuple, dict, set)) and not value:
        return None
    return value


def extract_diagnosis_fields(autofill_result: Dict[str, Any]) -> Dict[str, Any]:
    """Extract populated diagnosis fields from an insurance autofill result."""
    extracted = autofill_result.get("autofill_extracted") or {}
    diagnosis = extracted.get("diagnosis_and_procedures") or {}
    if not isinstance(diagnosis, dict):
        return {}

    updates: Dict[str, Any] = {}
    for field in DIAGNOSIS_FIELDS:
        value = _clean_value(diagnosis.get(field))
        if value is not None:
            updates[field] = value
    return updates


def save_diagnosis_fields_for_user(user_id: str, autofill_result: Dict[str, Any]) -> bool:
    """
    Best-effort save of extracted diagnosis fields to Users.

    Returns True when an update was written. Raises ClientError/unexpected
    exceptions to the caller so the router can log without failing autofill.
    """
    updates = extract_diagnosis_fields(autofill_result)
    if not updates:
        logger.info("[DIAGNOSIS_SAVE] No diagnosis fields to persist for user_id=%s", user_id)
        return False

    now_iso = datetime.now(timezone.utc).isoformat()
    updates["diagnosis_updated_at"] = now_iso

    expression_names: Dict[str, str] = {}
    expression_values: Dict[str, Any] = {}
    set_parts = []

    for index, (field, value) in enumerate(updates.items()):
        name_key = f"#f{index}"
        value_key = f":v{index}"
        expression_names[name_key] = field
        expression_values[value_key] = value
        set_parts.append(f"{name_key} = {value_key}")

    try:
        users_table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET " + ", ".join(set_parts),
            ExpressionAttributeNames=expression_names,
            ExpressionAttributeValues=expression_values,
            ConditionExpression="attribute_exists(user_id)",
        )
        logger.info(
            "[DIAGNOSIS_SAVE] Persisted diagnosis fields for user_id=%s fields=%s",
            user_id,
            sorted(updates.keys()),
        )
        return True
    except ClientError:
        logger.exception("[DIAGNOSIS_SAVE] DynamoDB update failed for user_id=%s", user_id)
        raise
