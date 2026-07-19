"""
User-doctor relationship layer (connectivity graph, not entitlements).

Subscriptions remain the source of truth for billing and booking limits. This
module writes the persistent doctor-patient link into the shared UserDoctor
junction table used by hospital APIs.
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
    """Idempotently ensure an ACTIVE (user, doctor) bond exists in UserDoctor."""
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


def sync_relation_for_subscription(
    user_id: str, doctor_id: str, subscription_id: str
) -> dict:
    """Called after subscription rows are created or returned (idempotent)."""
    return create_relation(
        user_id=user_id,
        doctor_id=doctor_id,
        relation_type=RelationType.USER_SUBSCRIPTION,
        linked_by=LinkedBy.SYSTEM,
        subscription_id=subscription_id,
    )


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
    """All ACTIVE bonds for a doctor (one row per patient), via UserDoctor."""
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


def deactivate_relation(user_id: str, doctor_id: str) -> dict:
    """Flip the (user, doctor) bond to INACTIVE. 404 if no active bond exists."""
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
