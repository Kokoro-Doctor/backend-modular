"""
User–doctor relationship layer (connectivity graph, not entitlements).

Subscriptions remain the source of truth for billing and booking limits.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Optional

import uuid
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError
from fastapi import HTTPException

from app.config import USER_DOCTOR_RELATIONS_TABLE
from app.logger import get_logger

logger = get_logger(__name__)


class RelationType:
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


def _query_relations_for_pair(user_id: str, doctor_id: str) -> List[dict]:
    """All relation items for this user+doctor (via GSI)."""
    items: List[dict] = []
    try:
        eks = None
        while True:
            kwargs: dict = {
                "IndexName": "GSI_UserRelations",
                "KeyConditionExpression": Key("user_id").eq(user_id)
                & Key("doctor_id").eq(doctor_id),
            }
            if eks:
                kwargs["ExclusiveStartKey"] = eks
            resp = USER_DOCTOR_RELATIONS_TABLE.query(**kwargs)
            items.extend(resp.get("Items", []))
            eks = resp.get("LastEvaluatedKey")
            if not eks:
                break
        return items
    except ClientError as e:
        logger.error(f"DynamoDB error querying relations for {user_id}/{doctor_id}: {e}")
        raise HTTPException(500, "Failed to query user–doctor relations")


def _pick_active(items: List[dict]) -> Optional[dict]:
    for it in items:
        if it.get("status") == RelationStatus.ACTIVE:
            return it
    return None


def get_relation_for_pair(
    user_id: str,
    doctor_id: str,
    *,
    active_only: bool = False,
) -> Optional[dict]:
    """Return the latest relation for a user+doctor pair, optionally only ACTIVE."""
    if not user_id or not doctor_id:
        raise HTTPException(400, "user_id and doctor_id are required")

    items = _query_relations_for_pair(user_id, doctor_id)
    if active_only:
        return _pick_active(items)
    if not items:
        return None
    items.sort(
        key=lambda x: x.get("updated_at") or x.get("created_at") or "",
        reverse=True,
    )
    return items[0]


def create_relation(
    user_id: str,
    doctor_id: str,
    relation_type: str,
    linked_by: str,
    hospital_id: Optional[str] = None,
    subscription_id: Optional[str] = None,
) -> dict:
    """
    Ensure an ACTIVE relation exists for user_id + doctor_id (no duplicate ACTIVE rows).

    - If ACTIVE exists: optionally refresh subscription_id / type / linked_by metadata.
    - If only INACTIVE exists: reactivate the most recently updated row.
    - Else: insert a new relation_id.
    """
    if not user_id or not doctor_id:
        raise HTTPException(400, "user_id and doctor_id are required")

    existing_all = _query_relations_for_pair(user_id, doctor_id)
    active = _pick_active(existing_all)

    now_iso = _now_iso()

    if active:
        updates: dict[str, Any] = {"updated_at": now_iso}
        expr_parts = ["#u = :u"]
        expr_names = {"#u": "updated_at"}
        expr_vals = {":u": now_iso}

        if subscription_id is not None:
            expr_parts.append("#sid = :sid")
            expr_names["#sid"] = "subscription_id"
            expr_vals[":sid"] = subscription_id
        if relation_type:
            expr_parts.append("#rt = :rt")
            expr_names["#rt"] = "relation_type"
            expr_vals[":rt"] = relation_type
        if linked_by:
            expr_parts.append("#lb = :lb")
            expr_names["#lb"] = "linked_by"
            expr_vals[":lb"] = linked_by
        if hospital_id is not None:
            expr_parts.append("#hid = :hid")
            expr_names["#hid"] = "hospital_id"
            expr_vals[":hid"] = hospital_id

        try:
            resp = USER_DOCTOR_RELATIONS_TABLE.update_item(
                Key={"relation_id": active["relation_id"]},
                UpdateExpression="SET " + ", ".join(expr_parts),
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_vals,
                ReturnValues="ALL_NEW",
            )
            out = resp.get("Attributes", active)
            logger.info(
                f"Updated existing relation {active['relation_id']} for user={user_id} doctor={doctor_id}"
            )
            return out
        except ClientError as e:
            logger.error(f"DynamoDB error updating relation: {e}")
            raise HTTPException(500, "Failed to update user–doctor relation")

    inactive = [i for i in existing_all if i.get("status") == RelationStatus.INACTIVE]
    if inactive:
        inactive.sort(
            key=lambda x: x.get("updated_at") or x.get("created_at") or "",
            reverse=True,
        )
        row = inactive[0]
        try:
            r_parts = [
                "#s = :active",
                "#u = :now",
                "relation_type = :rt",
                "linked_by = :lb",
            ]
            r_names = {"#s": "status", "#u": "updated_at"}
            r_vals = {
                ":active": RelationStatus.ACTIVE,
                ":now": now_iso,
                ":rt": relation_type,
                ":lb": linked_by,
            }
            if hospital_id is not None:
                r_parts.append("hospital_id = :hid")
                r_vals[":hid"] = hospital_id
            if subscription_id is not None:
                r_parts.append("subscription_id = :sid")
                r_vals[":sid"] = subscription_id
            r_resp = USER_DOCTOR_RELATIONS_TABLE.update_item(
                Key={"relation_id": row["relation_id"]},
                UpdateExpression="SET " + ", ".join(r_parts),
                ExpressionAttributeNames=r_names,
                ExpressionAttributeValues=r_vals,
                ReturnValues="ALL_NEW",
            )
            refreshed = r_resp.get("Attributes", row)
            logger.info(
                f"Reactivated relation {row['relation_id']} for user={user_id} doctor={doctor_id}"
            )
            return refreshed
        except ClientError as e:
            logger.error(f"DynamoDB error reactivating relation: {e}")
            raise HTTPException(500, "Failed to reactivate user–doctor relation")

    relation_id = str(uuid.uuid4())
    item = {
        "relation_id": relation_id,
        "user_id": user_id,
        "doctor_id": doctor_id,
        "relation_type": relation_type,
        "status": RelationStatus.ACTIVE,
        "linked_by": linked_by,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    if hospital_id is not None:
        item["hospital_id"] = hospital_id
    if subscription_id is not None:
        item["subscription_id"] = subscription_id

    try:
        USER_DOCTOR_RELATIONS_TABLE.put_item(Item=item)
        logger.info(f"Created relation {relation_id} user={user_id} doctor={doctor_id} type={relation_type}")
        return item
    except ClientError as e:
        logger.error(f"DynamoDB error creating relation: {e}")
        raise HTTPException(500, "Failed to create user–doctor relation")


def sync_relation_for_subscription(
    user_id: str, doctor_id: str, subscription_id: str
) -> dict:
    """Called after subscription rows are created or returned (idempotent payment, etc.)."""
    return create_relation(
        user_id=user_id,
        doctor_id=doctor_id,
        relation_type=RelationType.SUBSCRIPTION,
        linked_by=LinkedBy.SYSTEM,
        subscription_id=subscription_id,
    )


def get_user_doctors(user_id: str) -> List[dict]:
    """All ACTIVE relations for a user (one row per doctor)."""
    if not user_id:
        raise HTTPException(400, "user_id is required")
    items: List[dict] = []
    try:
        eks = None
        while True:
            kwargs: dict = {
                "IndexName": "GSI_UserRelations",
                "KeyConditionExpression": Key("user_id").eq(user_id),
                "FilterExpression": Attr("status").eq(RelationStatus.ACTIVE),
            }
            if eks:
                kwargs["ExclusiveStartKey"] = eks
            resp = USER_DOCTOR_RELATIONS_TABLE.query(**kwargs)
            items.extend(resp.get("Items", []))
            eks = resp.get("LastEvaluatedKey")
            if not eks:
                break
        return items
    except ClientError as e:
        logger.error(f"DynamoDB error get_user_doctors({user_id}): {e}")
        raise HTTPException(500, "Failed to list doctors for user")


def get_doctor_patients(doctor_id: str) -> List[dict]:
    """All ACTIVE relations for a doctor (one row per patient)."""
    if not doctor_id:
        raise HTTPException(400, "doctor_id is required")
    items: List[dict] = []
    try:
        eks = None
        while True:
            kwargs: dict = {
                "IndexName": "GSI_DoctorRelations",
                "KeyConditionExpression": Key("doctor_id").eq(doctor_id),
                "FilterExpression": Attr("status").eq(RelationStatus.ACTIVE),
            }
            if eks:
                kwargs["ExclusiveStartKey"] = eks
            resp = USER_DOCTOR_RELATIONS_TABLE.query(**kwargs)
            items.extend(resp.get("Items", []))
            eks = resp.get("LastEvaluatedKey")
            if not eks:
                break
        return items
    except ClientError as e:
        logger.error(f"DynamoDB error get_doctor_patients({doctor_id}): {e}")
        raise HTTPException(500, "Failed to list patients for doctor")


def list_active_relations_for_hospital(hospital_id: str) -> List[dict]:
    """All ACTIVE relations assigned through a hospital."""
    hid = (hospital_id or "").strip()
    if not hid:
        raise HTTPException(400, "hospital_id is required")

    items: List[dict] = []
    try:
        eks = None
        while True:
            kwargs: dict = {
                "FilterExpression": Attr("hospital_id").eq(hid)
                & Attr("status").eq(RelationStatus.ACTIVE),
            }
            if eks:
                kwargs["ExclusiveStartKey"] = eks
            resp = USER_DOCTOR_RELATIONS_TABLE.scan(**kwargs)
            items.extend(resp.get("Items", []))
            eks = resp.get("LastEvaluatedKey")
            if not eks:
                break
        return items
    except ClientError as e:
        logger.error(f"DynamoDB error list_active_relations_for_hospital({hid}): {e}")
        raise HTTPException(500, "Failed to list hospital relations")


def get_active_hospital_assigned_doctor(user_id: str, hospital_id: str) -> Optional[dict]:
    """Return the single active HOSPITAL_ASSIGNED relation for a user at the given hospital, or None."""
    relations = get_user_doctors(user_id)
    for r in relations:
        if (
            r.get("relation_type") == RelationType.HOSPITAL_ASSIGNED
            and r.get("hospital_id") == hospital_id
        ):
            return r
    return None


def deactivate_relation(user_id: str, doctor_id: str) -> dict:
    """Set status INACTIVE for the ACTIVE relation between user and doctor."""
    if not user_id or not doctor_id:
        raise HTTPException(400, "user_id and doctor_id are required")
    active = _pick_active(_query_relations_for_pair(user_id, doctor_id))
    if not active:
        raise HTTPException(404, "No active relation found for this user and doctor")
    now_iso = _now_iso()
    try:
        d_resp = USER_DOCTOR_RELATIONS_TABLE.update_item(
            Key={"relation_id": active["relation_id"]},
            UpdateExpression="SET #s = :inactive, #u = :now",
            ExpressionAttributeNames={"#s": "status", "#u": "updated_at"},
            ExpressionAttributeValues={
                ":inactive": RelationStatus.INACTIVE,
                ":now": now_iso,
            },
            ReturnValues="ALL_NEW",
        )
        out = d_resp.get("Attributes", active)
        logger.info(f"Deactivated relation {active['relation_id']} user={user_id} doctor={doctor_id}")
        return out
    except ClientError as e:
        logger.error(f"DynamoDB error deactivate_relation: {e}")
        raise HTTPException(500, "Failed to deactivate user–doctor relation")
