"""
Read diagnosis summary fields from Users for hospital workflows.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException

from app.config import USERS_TABLE
from app.logger import get_logger

logger = get_logger(__name__)

DIAGNOSIS_FIELDS = (
    "primary_diagnosis",
    "primary_icd_code",
    "additional_diagnosis",
    "additional_icd_code",
    "diagnosis_updated_at",
)


def get_user_diagnosis_summary(user_id: str) -> Dict[str, Any]:
    """Return diagnosis summary fields for a user, or raise 404 if absent."""
    uid = (user_id or "").strip()
    if not uid:
        raise HTTPException(status_code=400, detail="user_id is required")

    try:
        response = USERS_TABLE.get_item(Key={"user_id": uid})
    except Exception as exc:
        logger.error(
            "[diagnosis_summary] Failed to load user_id=%r from Users: %r",
            uid,
            exc,
        )
        raise HTTPException(status_code=500, detail="Failed to load user") from exc

    user = response.get("Item")
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    summary: Dict[str, Any] = {"user_id": uid}
    for field in DIAGNOSIS_FIELDS:
        summary[field] = user.get(field)

    logger.info("[diagnosis_summary] Loaded diagnosis summary for user_id=%s", uid)
    return summary
