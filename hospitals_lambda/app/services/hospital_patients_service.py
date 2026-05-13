"""
List Users (patients) affiliated with a hospital via Users.hospital_id (GSI hospital_id-index).
"""
from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional, Tuple

from boto3.dynamodb.conditions import Key
from fastapi import HTTPException

from app.config import USERS_TABLE
from app.logger import get_logger

logger = get_logger(__name__)

HOSPITAL_ID_INDEX = "hospital_id-index"


def encode_exclusive_start_key(key: Optional[dict]) -> Optional[str]:
    if not key:
        return None
    raw = json.dumps(key, default=str, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_exclusive_start_key(cursor: Optional[str]) -> Optional[dict]:
    if not cursor or not str(cursor).strip():
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("cursor must decode to an object")
        return parsed
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid cursor")


def list_patients_by_hospital(
    hospital_id: str,
    *,
    limit: int,
    exclusive_start_key: Optional[dict] = None,
) -> Tuple[List[dict], Optional[dict]]:
    hid = (hospital_id or "").strip()
    kwargs: Dict[str, Any] = {
        "IndexName": HOSPITAL_ID_INDEX,
        "KeyConditionExpression": Key("hospital_id").eq(hid),
        "Limit": limit,
    }
    if exclusive_start_key:
        kwargs["ExclusiveStartKey"] = exclusive_start_key
    try:
        resp = USERS_TABLE.query(**kwargs)
    except Exception as e:
        logger.error(f"[list_patients_by_hospital] query failed hospital_id={hid!r}: {e!r}")
        raise HTTPException(status_code=500, detail="Failed to list patients")
    return resp.get("Items", []), resp.get("LastEvaluatedKey")
