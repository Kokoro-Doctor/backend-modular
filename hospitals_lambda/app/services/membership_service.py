"""
Hospital-affiliation junction services (many-to-many):

  - UserHospital   : patient <-> hospital
  - DoctorHospital : doctor  <-> hospital

Both relationships share the same shape — a member affiliated with a hospital —
so the helpers are symmetric. Linking is additive and idempotent: it never
removes a member's other affiliations. The composite primary key
(hospital_id + member_id) makes each write an upsert, so re-linking the same
pair cannot create a duplicate row; `linked_at` is preserved via if_not_exists.

Base table  : PK hospital_id, SK member_id  → members of a hospital
Reverse GSI : PK member_id,  SK hospital_id → hospitals of a member
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from boto3.dynamodb.conditions import Key
from fastapi import HTTPException

from app.config import USER_HOSPITAL_TABLE, DOCTOR_HOSPITAL_TABLE
from app.logger import get_logger

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _link(table, member_key: str, member_id: str, hospital_id: str,
          hospital_name: str, source: str) -> None:
    if not member_id or not hospital_id:
        raise HTTPException(400, f"{member_key} and hospital_id are required")
    now = _now()
    try:
        table.update_item(
            Key={"hospital_id": hospital_id, member_key: member_id},
            UpdateExpression=(
                "SET #s = :active, #src = :src, hospital_name = :hn, "
                "updated_at = :now, linked_at = if_not_exists(linked_at, :now)"
            ),
            ExpressionAttributeNames={"#s": "status", "#src": "source"},
            ExpressionAttributeValues={
                ":active": "ACTIVE",
                ":src": source,
                ":hn": hospital_name or "",
                ":now": now,
            },
        )
        logger.info(
            "[membership] linked %s=%s to hospital_id=%s (source=%s)",
            member_key, member_id, hospital_id, source,
        )
    except Exception as e:
        logger.error(
            "[membership] link failed %s=%r hospital_id=%r error=%r",
            member_key, member_id, hospital_id, e,
        )
        raise HTTPException(500, "Failed to link member to hospital")


def _member_ids_for_hospital(table, member_key: str, hospital_id: str) -> List[str]:
    hid = (hospital_id or "").strip()
    if not hid:
        raise HTTPException(400, "hospital_id is required")
    ids: List[str] = []
    try:
        eks = None
        while True:
            kwargs: dict = {"KeyConditionExpression": Key("hospital_id").eq(hid)}
            if eks:
                kwargs["ExclusiveStartKey"] = eks
            resp = table.query(**kwargs)
            for item in resp.get("Items", []):
                if item.get("status", "ACTIVE") == "ACTIVE" and item.get(member_key):
                    ids.append(item[member_key])
            eks = resp.get("LastEvaluatedKey")
            if not eks:
                break
        return ids
    except Exception as e:
        logger.error("[membership] list members failed hospital_id=%r: %r", hid, e)
        raise HTTPException(500, "Failed to list hospital members")


def _is_member(table, member_key: str, member_id: str, hospital_id: str) -> bool:
    if not member_id or not hospital_id:
        return False
    try:
        resp = table.get_item(Key={"hospital_id": hospital_id, member_key: member_id})
        item = resp.get("Item")
        return bool(item) and item.get("status", "ACTIVE") == "ACTIVE"
    except Exception as e:
        logger.error(
            "[membership] is_member failed %s=%r hospital_id=%r: %r",
            member_key, member_id, hospital_id, e,
        )
        return False


# --------------------------- UserHospital ---------------------------

def link_user_hospital(user_id: str, hospital_id: str, *,
                       hospital_name: str = "", source: str = "staff") -> None:
    _link(USER_HOSPITAL_TABLE, "user_id", user_id, hospital_id, hospital_name, source)


def list_user_ids_for_hospital(hospital_id: str) -> List[str]:
    return _member_ids_for_hospital(USER_HOSPITAL_TABLE, "user_id", hospital_id)


def is_user_in_hospital(user_id: str, hospital_id: str) -> bool:
    return _is_member(USER_HOSPITAL_TABLE, "user_id", user_id, hospital_id)


# --------------------------- DoctorHospital ---------------------------

def link_doctor_hospital(doctor_id: str, hospital_id: str, *,
                         hospital_name: str = "", source: str = "staff") -> None:
    _link(DOCTOR_HOSPITAL_TABLE, "doctor_id", doctor_id, hospital_id, hospital_name, source)


def list_doctor_ids_for_hospital(hospital_id: str) -> List[str]:
    return _member_ids_for_hospital(DOCTOR_HOSPITAL_TABLE, "doctor_id", hospital_id)


def is_doctor_in_hospital(doctor_id: str, hospital_id: str) -> bool:
    return _is_member(DOCTOR_HOSPITAL_TABLE, "doctor_id", doctor_id, hospital_id)
