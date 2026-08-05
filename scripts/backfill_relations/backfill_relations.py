"""
One-time backfill: copy doctor-patient links into the unified UserDoctor table.

Sources:
  - UserDoctorRelations, the legacy relation table used by older booking code.
  - UserDoctorSubscriptions, the entitlement table used by subscription flows.

Usage:
    AWS_REGION=ap-south-1 python backfill_relations.py [--dry-run]

Flags:
    --dry-run   Print what would be upserted without writing to DynamoDB.
"""
import argparse
import os
from datetime import datetime, timezone
from typing import Any, Iterable

import boto3

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)

USER_DOCTOR_TABLE = dynamodb.Table(os.environ.get("USER_DOCTOR_TABLE", "UserDoctor"))
SUBSCRIPTIONS_TABLE = dynamodb.Table(
    os.environ.get("USER_DOCTOR_SUBSCRIPTIONS_TABLE", "UserDoctorSubscriptions")
)
LEGACY_RELATIONS_TABLE = dynamodb.Table(
    os.environ.get("USER_DOCTOR_RELATIONS_TABLE", "UserDoctorRelations")
)

ACTIVE = "ACTIVE"
USER_SUBSCRIPTION = "USER_SUBSCRIPTION"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scan_all(table) -> Iterable[dict]:
    params: dict[str, Any] = {}
    while True:
        resp = table.scan(**params)
        yield from resp.get("Items", [])
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        params["ExclusiveStartKey"] = lek


def _upsert_user_doctor(
    *,
    user_id: str,
    doctor_id: str,
    relation_type: str,
    linked_by: str,
    status: str = ACTIVE,
    hospital_id: str | None = None,
    subscription_id: str | None = None,
    created_at: str | None = None,
    dry_run: bool = False,
) -> bool:
    if not user_id or not doctor_id:
        return False

    now_iso = _now_iso()
    created_value = created_at or now_iso
    set_parts = [
        "#s = :status",
        "relation_type = :rt",
        "linked_by = :lb",
        "updated_at = :now",
        "created_at = if_not_exists(created_at, :created)",
    ]
    names = {"#s": "status"}
    vals: dict[str, Any] = {
        ":status": status or ACTIVE,
        ":rt": relation_type,
        ":lb": linked_by,
        ":now": now_iso,
        ":created": created_value,
    }
    if hospital_id:
        set_parts.append("hospital_id = :hid")
        vals[":hid"] = hospital_id
    if subscription_id:
        set_parts.append("subscription_id = :sid")
        vals[":sid"] = subscription_id

    if dry_run:
        print(
            f"[DRY-RUN] user={user_id} doctor={doctor_id} "
            f"type={relation_type} linked_by={linked_by}"
        )
        return True

    USER_DOCTOR_TABLE.update_item(
        Key={"user_id": user_id, "doctor_id": doctor_id},
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=vals,
    )
    print(f"[UPSERTED] user={user_id} doctor={doctor_id} type={relation_type}")
    return True


def _backfill_legacy_relations(dry_run: bool) -> int:
    count = 0
    for rel in _scan_all(LEGACY_RELATIONS_TABLE):
        if _upsert_user_doctor(
            user_id=rel.get("user_id"),
            doctor_id=rel.get("doctor_id"),
            relation_type=rel.get("relation_type") or "MANUAL",
            linked_by=rel.get("linked_by") or "system",
            status=rel.get("status") or ACTIVE,
            hospital_id=rel.get("hospital_id"),
            subscription_id=rel.get("subscription_id"),
            created_at=rel.get("created_at"),
            dry_run=dry_run,
        ):
            count += 1
    return count


def _backfill_subscriptions(dry_run: bool) -> int:
    count = 0
    seen_pairs: set[tuple[str, str]] = set()
    for sub in _scan_all(SUBSCRIPTIONS_TABLE):
        user_id = sub.get("user_id")
        doctor_id = sub.get("doctor_id")
        if not user_id or not doctor_id:
            continue
        pair = (user_id, doctor_id)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)

        if _upsert_user_doctor(
            user_id=user_id,
            doctor_id=doctor_id,
            relation_type=USER_SUBSCRIPTION,
            linked_by="system",
            status=ACTIVE,
            subscription_id=sub.get("subscription_id"),
            created_at=sub.get("created_at") or sub.get("start_date"),
            dry_run=dry_run,
        ):
            count += 1
    return count


def backfill(dry_run: bool = False):
    legacy_count = _backfill_legacy_relations(dry_run=dry_run)
    subscription_count = _backfill_subscriptions(dry_run=dry_run)
    print(
        "\nDone. "
        f"legacy_relations_processed={legacy_count} "
        f"subscription_pairs_processed={subscription_count}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill unified UserDoctor table")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without writing")
    args = parser.parse_args()
    backfill(dry_run=args.dry_run)
