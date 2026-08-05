"""
Patient <-> Hospital membership (UserHospital junction).

A user can belong to many hospitals. Linking is additive and idempotent: it
never removes the user's other hospital memberships. The composite primary key
(hospital_id + user_id) makes the write an upsert, so a re-link can't create a
duplicate row. `linked_at` is preserved across re-links via if_not_exists.
"""
from datetime import datetime, timezone

from app import config
from app.logger import get_logger

logger = get_logger(__name__)


def link_user_hospital(user_id: str, hospital_id: str, *, source: str = "abha") -> None:
    """Ensure a UserHospital membership row exists for (hospital_id, user_id).

    Best-effort: failures are logged, not raised — the caller's user/auth records
    are already created and a missing membership can be re-linked on next login.
    """
    if not user_id or not hospital_id:
        logger.warning(
            "[membership] link_user_hospital skipped (missing id) user_id=%r hospital_id=%r",
            user_id, hospital_id,
        )
        return
    now = datetime.now(timezone.utc).isoformat()
    try:
        config.user_hospital_table.update_item(
            Key={"hospital_id": hospital_id, "user_id": user_id},
            UpdateExpression=(
                "SET #s = :active, #src = :src, updated_at = :now, "
                "linked_at = if_not_exists(linked_at, :now)"
            ),
            ExpressionAttributeNames={"#s": "status", "#src": "source"},
            ExpressionAttributeValues={
                ":active": "ACTIVE",
                ":src": source,
                ":now": now,
            },
        )
        logger.info(
            "[membership] linked user_id=%s to hospital_id=%s (source=%s)",
            user_id, hospital_id, source,
        )
    except Exception:
        logger.exception(
            "[membership] Failed to link user_id=%s hospital_id=%s", user_id, hospital_id
        )
