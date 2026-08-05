"""
ConsentArtefacts DynamoDB service — Milestone 2 Data Flow (Section 6).

When ABDM grants a consent it pushes the full consent artefact to our 6.3.1
callback (/api/v3/consent/request/hip/notify). We persist it here keyed by
consentId, because the *later* health-information request (6.3.3) only sends
the consentId — to actually build and push the data we need the artefact's
careContexts, hiTypes and permission window, which only arrived in 6.3.1.

Table: ConsentArtefacts
  PK:  consent_id
  TTL: ttl   (epoch seconds; rows auto-expire after the permission erase window
             or a default retention period, whichever we set)

The full artefact + signature are stored verbatim as a JSON string so nothing
ABDM sent is lost. Convenience columns (status, hip_id, patient, hiu_id,
notify_request_id) are denormalised for quick lookups and so the data-flow
notify (6.3.6) can echo the right ids.
"""
import json
from datetime import datetime, timezone, timedelta
from typing import Any, List, Optional

from app import config
from app.logger import get_logger

logger = get_logger(__name__)

# Keep an artefact around well past the typical permission window for auditing.
_CONSENT_TTL_DAYS = 180


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ttl_epoch() -> int:
    return int((_now() + timedelta(days=_CONSENT_TTL_DAYS)).timestamp())


def _dump(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def _load(value: Optional[str]) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


# ---------------------------------------------------------------------------
# Write — at 6.3.1 callback time
# ---------------------------------------------------------------------------

def save(
    consent_id: str,
    status: str,
    consent_detail: Optional[dict],
    hip_id: Optional[str],
    notify_request_id: Optional[str],
    signature: Optional[str] = None,
) -> None:
    """
    Upsert a consent artefact received from the 6.3.1 callback.

    notify_request_id is the REQUEST-ID header of the 6.3.1 callback — we must
    echo it back as response.requestId in the 6.3.2 acknowledgement.
    """
    now = _now()
    detail = consent_detail or {}

    patient = None
    if isinstance(detail.get("patient"), dict):
        patient = detail["patient"].get("id")
    hiu_id = None
    if isinstance(detail.get("hiu"), dict):
        hiu_id = detail["hiu"].get("id")

    fields = {
        "status":            status,
        "consent_detail":    _dump(detail),
        "hip_id":            hip_id,
        "hiu_id":            hiu_id,
        "patient":           patient,
        "notify_request_id": notify_request_id,
        "signature":         signature,
        "updated_at":        now.isoformat(),
        "ttl":               _ttl_epoch(),
    }
    fields = {k: v for k, v in fields.items() if v is not None}

    set_parts = [f"#{k} = :{k}" for k in fields]
    names     = {f"#{k}": k for k in fields}
    values    = {f":{k}": v for k, v in fields.items()}

    set_parts.append("#created_at = if_not_exists(#created_at, :created_at)")
    names["#created_at"]  = "created_at"
    values[":created_at"] = now.isoformat()

    config.consent_artefacts_table.update_item(
        Key={"consent_id": consent_id},
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )
    logger.info(
        "[ConsentService] Saved consent_id=%s status=%s hip_id=%s patient=%s",
        consent_id, status, hip_id, patient,
    )


def mark_acknowledged(
    consent_id: str,
    ack_status: str,
    ack_request_id: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """
    Record the outcome of the 6.3.2 on-notify acknowledgement on the artefact row.

    6.3.2 is fire-and-forget (ABDM never calls back), so without this there is no
    durable trace that we acknowledged a consent at all. `ack_status` is "OK" once
    ABDM accepts the POST, "FAILED" otherwise; `ack_request_id` is the REQUEST-ID
    header we sent on that POST, which is what ABDM support will ask for.

    Best-effort: never raises, so a bookkeeping failure can't mask the real
    outcome of the acknowledgement itself.
    """
    fields = {
        "ack_status":     ack_status,
        "ack_request_id": ack_request_id,
        "ack_error":      error,
        "ack_at":         _now().isoformat(),
    }
    fields = {k: v for k, v in fields.items() if v is not None}

    try:
        config.consent_artefacts_table.update_item(
            Key={"consent_id": consent_id},
            UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
            ExpressionAttributeNames={f"#{k}": k for k in fields},
            ExpressionAttributeValues={f":{k}": v for k, v in fields.items()},
        )
        logger.info(
            "[ConsentService] consent_id=%s ack_status=%s ack_request_id=%s",
            consent_id, ack_status, ack_request_id,
        )
    except Exception:
        logger.exception(
            "[ConsentService] failed to record ack outcome consent_id=%s ack_status=%s",
            consent_id, ack_status,
        )


def update_status(consent_id: str, status: str) -> None:
    """Update only the status (e.g. GRANTED → REVOKED on a later callback)."""
    config.consent_artefacts_table.update_item(
        Key={"consent_id": consent_id},
        UpdateExpression="SET #s = :s, #u = :u",
        ExpressionAttributeNames={"#s": "status", "#u": "updated_at"},
        ExpressionAttributeValues={":s": status, ":u": _now().isoformat()},
    )
    logger.info("[ConsentService] consent_id=%s status -> %s", consent_id, status)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get(consent_id: str) -> Optional[dict]:
    """Return the raw row for consent_id, or None."""
    resp = config.consent_artefacts_table.get_item(Key={"consent_id": consent_id})
    return resp.get("Item")


def get_hydrated(consent_id: str) -> Optional[dict]:
    """Like get() but with consent_detail parsed back into an object."""
    item = get(consent_id)
    if not item:
        return None
    out = dict(item)
    if "consent_detail" in out:
        out["consent_detail"] = _load(out["consent_detail"])
    return out


def get_care_context_references(consent_id: str) -> List[str]:
    """
    Pull the careContextReference values out of a stored consent artefact.
    Used to build the data-push entries (6.3.5) and the notify status
    responses (6.3.6). Returns [] if the artefact or its careContexts are absent.
    """
    record = get_hydrated(consent_id)
    if not record:
        return []
    detail = record.get("consent_detail") or {}
    care_contexts = detail.get("careContexts") or []
    refs = []
    for cc in care_contexts:
        if isinstance(cc, dict) and cc.get("careContextReference"):
            refs.append(cc["careContextReference"])
    return refs
