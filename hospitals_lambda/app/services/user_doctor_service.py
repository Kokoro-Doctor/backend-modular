"""
User <-> Doctor bond (UserDoctor junction), hospital-independent.

One row per (user, doctor) pair — the bond is to the doctor as a person, not to
doctor@hospital. The composite primary key (user_id + doctor_id) makes writes
idempotent upserts, so a pair can never be duplicated and there is no
relation_id to manage. Hospital context (the assigning hospital, for
HOSPITAL_ASSIGNED links) rides along as the non-key `hospital_id` attribute.

Base table  : PK user_id,   SK doctor_id → doctors of a user
Reverse GSI : PK doctor_id, SK user_id   → patients of a doctor (GSI_DoctorUsers)

"Removing" a doctor flips status to INACTIVE rather than deleting the row, so
history (created_at, origin hospital) survives.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Optional

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError
from fastapi import HTTPException

from app.config import USER_DOCTOR_TABLE
from app.logger import get_logger

logger = get_logger(__name__)


class RelationType:
    USER_SUBSCRIPTION = "USER_SUBSCRIPTION"
    SUBSCRIPTION = "SUBSCRIPTION"
    HOSPITAL_ASSIGNED = "HOSPITAL_ASSIGNED"
    MANUAL = "MANUAL"


class RelationStatus:
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class LinkedBy:
    SYSTEM = "system"
    HOSPITAL_STAFF = "hospital_staff"
    DOCTOR = "doctor"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_relation(
    user_id: str,
    doctor_id: str,
    relation_type: str,
    linked_by: str,
    hospital_id: Optional[str] = None,
    subscription_id: Optional[str] = None,
) -> dict:
    """Idempotently ensure an ACTIVE (user, doctor) bond exists.

    Upserts the single row for the pair: sets status ACTIVE and refreshes
    metadata, preserving created_at across re-links.
    """
    if not user_id or not doctor_id:
        raise HTTPException(400, "user_id and doctor_id are required")

    now_iso = _now_iso()
    set_parts = [
        "#s = :active",
        "relation_type = :rt",
        "linked_by = :lb",
        "updated_at = :now",
        "created_at = if_not_exists(created_at, :now)",
    ]
    names = {"#s": "status"}
    vals: dict[str, Any] = {
        ":active": RelationStatus.ACTIVE,
        ":rt": relation_type,
        ":lb": linked_by,
        ":now": now_iso,
    }
    if hospital_id is not None:
        set_parts.append("hospital_id = :hid")
        vals[":hid"] = hospital_id
    if subscription_id is not None:
        set_parts.append("subscription_id = :sid")
        vals[":sid"] = subscription_id

    try:
        resp = USER_DOCTOR_TABLE.update_item(
            Key={"user_id": user_id, "doctor_id": doctor_id},
            UpdateExpression="SET " + ", ".join(set_parts),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=vals,
            ReturnValues="ALL_NEW",
        )
        out = resp.get("Attributes", {})
        logger.info(f"Linked user={user_id} doctor={doctor_id} type={relation_type}")
        return out
    except ClientError as e:
        logger.error(f"DynamoDB error create_relation: {e}")
        raise HTTPException(500, "Failed to create user-doctor relation")


def get_relation_for_pair(
    user_id: str, doctor_id: str, active_only: bool = False
) -> Optional[dict]:
    if not user_id or not doctor_id:
        raise HTTPException(400, "user_id and doctor_id are required")
    try:
        resp = USER_DOCTOR_TABLE.get_item(Key={"user_id": user_id, "doctor_id": doctor_id})
    except ClientError as e:
        logger.error(f"DynamoDB error get_relation_for_pair: {e}")
        raise HTTPException(500, "Failed to load user-doctor relation")
    item = resp.get("Item")
    if not item:
        return None
    if active_only and item.get("status") != RelationStatus.ACTIVE:
        return None
    return item


def get_user_doctors(user_id: str) -> List[dict]:
    """All ACTIVE bonds for a user (one row per doctor)."""
    if not user_id:
        raise HTTPException(400, "user_id is required")
    items: List[dict] = []
    try:
        eks = None
        while True:
            kwargs: dict = {
                "KeyConditionExpression": Key("user_id").eq(user_id),
                "FilterExpression": Attr("status").eq(RelationStatus.ACTIVE),
            }
            if eks:
                kwargs["ExclusiveStartKey"] = eks
            resp = USER_DOCTOR_TABLE.query(**kwargs)
            items.extend(resp.get("Items", []))
            eks = resp.get("LastEvaluatedKey")
            if not eks:
                break
        return items
    except ClientError as e:
        logger.error(f"DynamoDB error get_user_doctors({user_id}): {e}")
        raise HTTPException(500, "Failed to list doctors for user")


def get_doctor_patients(doctor_id: str) -> List[dict]:
    """All ACTIVE bonds for a doctor (one row per patient), via reverse GSI."""
    if not doctor_id:
        raise HTTPException(400, "doctor_id is required")
    items: List[dict] = []
    try:
        eks = None
        while True:
            kwargs: dict = {
                "IndexName": "GSI_DoctorUsers",
                "KeyConditionExpression": Key("doctor_id").eq(doctor_id),
                "FilterExpression": Attr("status").eq(RelationStatus.ACTIVE),
            }
            if eks:
                kwargs["ExclusiveStartKey"] = eks
            resp = USER_DOCTOR_TABLE.query(**kwargs)
            items.extend(resp.get("Items", []))
            eks = resp.get("LastEvaluatedKey")
            if not eks:
                break
        return items
    except ClientError as e:
        logger.error(f"DynamoDB error get_doctor_patients({doctor_id}): {e}")
        raise HTTPException(500, "Failed to list patients for doctor")


def get_active_hospital_assigned_doctor(user_id: str, hospital_id: str) -> Optional[dict]:
    """The active doctor bond for a user originating at this hospital, or None."""
    for r in get_user_doctors(user_id):
        if r.get("hospital_id") == hospital_id:
            return r
    return None


def deactivate_relation(user_id: str, doctor_id: str) -> dict:
    """Flip the (user, doctor) bond to INACTIVE. 404 if no active bond exists."""
    if not user_id or not doctor_id:
        raise HTTPException(400, "user_id and doctor_id are required")
    active = get_relation_for_pair(user_id, doctor_id, active_only=True)
    if not active:
        raise HTTPException(404, "No active relation found for this user and doctor")
    now_iso = _now_iso()
    try:
        resp = USER_DOCTOR_TABLE.update_item(
            Key={"user_id": user_id, "doctor_id": doctor_id},
            UpdateExpression="SET #s = :inactive, updated_at = :now",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":inactive": RelationStatus.INACTIVE,
                ":now": now_iso,
            },
            ReturnValues="ALL_NEW",
        )
        out = resp.get("Attributes", active)
        logger.info(f"Deactivated relation user={user_id} doctor={doctor_id}")
        return out
    except ClientError as e:
        logger.error(f"DynamoDB error deactivate_relation: {e}")
        raise HTTPException(500, "Failed to deactivate user-doctor relation")


def remove_hospital_assignment(user_id: str, doctor_id: str, hospital_id: str) -> dict:
    """Remove a hospital assignment without breaking an independent subscription link."""
    active = get_relation_for_pair(user_id, doctor_id, active_only=True)
    if not active:
        raise HTTPException(404, "No active relation found for this user and doctor")
    if active.get("hospital_id") != hospital_id:
        return active

    has_subscription_link = bool(active.get("subscription_id")) or active.get("relation_type") in {
        RelationType.USER_SUBSCRIPTION,
        RelationType.SUBSCRIPTION,
    }
    if not has_subscription_link:
        return deactivate_relation(user_id, doctor_id)

    now_iso = _now_iso()
    try:
        resp = USER_DOCTOR_TABLE.update_item(
            Key={"user_id": user_id, "doctor_id": doctor_id},
            UpdateExpression=(
                "SET relation_type = :rt, linked_by = :lb, updated_at = :now "
                "REMOVE hospital_id"
            ),
            ExpressionAttributeValues={
                ":rt": RelationType.USER_SUBSCRIPTION,
                ":lb": LinkedBy.SYSTEM,
                ":now": now_iso,
            },
            ReturnValues="ALL_NEW",
        )
        out = resp.get("Attributes", active)
        logger.info(
            f"Removed hospital assignment but preserved subscription relation "
            f"user={user_id} doctor={doctor_id} hospital={hospital_id}"
        )
        return out
    except ClientError as e:
        logger.error(f"DynamoDB error remove_hospital_assignment: {e}")
        raise HTTPException(500, "Failed to remove hospital assignment")
