"""
AbdmTransactions DynamoDB service — tracking for ABDM async (callback-based) APIs.

Why this exists
───────────────
The HIP-linking APIs are asynchronous: we POST a request, get a bare 202, and the
real answer arrives later on a webhook. Without persistence we can't answer the
basic operational questions — "did ABDM call us back? when? with what?" — without
grepping mixed CloudWatch logs.

This table is the single source of truth for every async exchange:

  1. At request time  (4.3.1 generate-token / 4.3.3 link-carecontext)
        → write a PENDING row keyed by the REQUEST-ID we send to ABDM.
  2. At callback time (4.3.2 / 4.3.4)
        → correlate on response.requestId (ABDM echoes our REQUEST-ID back),
          flip the row to COMPLETED / FAILED and store the full callback body.

A PENDING row that never flips = ABDM never called back.
A COMPLETED/FAILED row = exactly when and what we received.

Synchronous M1 identity APIs (create / login / OTP) return inline and do NOT
use this table.

Table: AbdmTransactions
  PK:  request_id            (the REQUEST-ID header we send to ABDM)
  GSI: hip_id-index          (hip_id HASH, created_at RANGE)
  GSI: abha_address-index    (abha_address HASH, created_at RANGE)
  TTL: ttl                   (epoch seconds; auto-cleanup after N days)

Payloads are stored as JSON strings to sidestep DynamoDB's float/empty-value
quirks and to keep arbitrary request/response shapes intact.
"""
import json
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from boto3.dynamodb.conditions import Attr, Key

from app import config
from app.logger import get_logger

logger = get_logger(__name__)

# Status values
PENDING = "PENDING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ttl_epoch() -> int:
    return int((_now() + timedelta(days=config.ABDM_TRANSACTION_TTL_DAYS)).timestamp())


def _dump(value: Any) -> Optional[str]:
    """Serialise an arbitrary payload to a JSON string for safe DynamoDB storage."""
    if value is None:
        return None
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def _load(value: Optional[str]) -> Any:
    """Inverse of _dump — used when reading rows back out for the admin view."""
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


# ---------------------------------------------------------------------------
# Write — request time
# ---------------------------------------------------------------------------

def create_pending(
    request_id: str,
    api: str,
    hospital_id: str,
    hip_id: str,
    abha_address: Optional[str],
    request_payload: Any = None,
) -> None:
    """
    Record an outbound async ABDM request as PENDING, keyed by the REQUEST-ID
    we are about to send. Called immediately before the ABDM call.

    `api` discriminates the flow: "generate-token" | "link-carecontext" | ...
    """
    now = _now()
    item = {
        "request_id":      request_id,
        "api":             api,
        "hospital_id":     hospital_id,
        "hip_id":          hip_id,
        "status":          PENDING,
        "request_payload": _dump(request_payload),
        "created_at":      now.isoformat(),
        "updated_at":      now.isoformat(),
        "ttl":             _ttl_epoch(),
    }
    if abha_address:
        item["abha_address"] = abha_address
    # drop None values — DynamoDB rejects them
    item = {k: v for k, v in item.items() if v is not None}

    config.abdm_transactions_table.put_item(Item=item)
    logger.info(
        "[AbdmTxn] PENDING request_id=%s api=%s hip_id=%s abha_address=%s",
        request_id, api, hip_id, abha_address,
    )


# ---------------------------------------------------------------------------
# Write — callback time
# ---------------------------------------------------------------------------

def mark_completed(request_id: str, callback_payload: Any = None) -> None:
    """Flip a transaction to COMPLETED and store the callback body."""
    _finalise(request_id, COMPLETED, callback_payload=callback_payload)


def mark_failed(
    request_id: str,
    error: Any = None,
    callback_payload: Any = None,
) -> None:
    """Flip a transaction to FAILED, storing the error and callback body."""
    _finalise(request_id, FAILED, error=error, callback_payload=callback_payload)


def _finalise(
    request_id: str,
    status: str,
    error: Any = None,
    callback_payload: Any = None,
) -> None:
    """
    Update an existing PENDING row to a terminal status. If no row exists for
    this request_id (e.g. a callback we never recorded a request for), we still
    persist the callback as an 'orphan' so nothing is silently dropped.
    """
    now = _now()
    existing = get(request_id)

    if not existing:
        logger.warning(
            "[AbdmTxn] callback for unknown request_id=%s — storing as orphan", request_id
        )
        item = {
            "request_id":          request_id,
            "api":                 "unknown",
            "status":              status,
            "orphan_callback":     True,
            "callback_payload":    _dump(callback_payload),
            "callback_received_at": now.isoformat(),
            "created_at":          now.isoformat(),
            "updated_at":          now.isoformat(),
            "ttl":                 _ttl_epoch(),
        }
        if error is not None:
            item["error"] = _dump(error)
        item = {k: v for k, v in item.items() if v is not None}
        config.abdm_transactions_table.put_item(Item=item)
        return

    fields = {
        "status":               status,
        "callback_payload":     _dump(callback_payload),
        "callback_received_at": now.isoformat(),
        "updated_at":           now.isoformat(),
    }
    if error is not None:
        fields["error"] = _dump(error)
    fields = {k: v for k, v in fields.items() if v is not None}

    set_parts = [f"#{k} = :{k}" for k in fields]
    names     = {f"#{k}": k for k in fields}
    values    = {f":{k}": v for k, v in fields.items()}

    config.abdm_transactions_table.update_item(
        Key={"request_id": request_id},
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )
    logger.info("[AbdmTxn] %s request_id=%s", status, request_id)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get(request_id: str) -> Optional[dict]:
    """Return the raw transaction row for request_id, or None."""
    resp = config.abdm_transactions_table.get_item(Key={"request_id": request_id})
    return resp.get("Item")


def get_hydrated(request_id: str) -> Optional[dict]:
    """Like get(), but with request_payload/callback_payload/error parsed back to objects."""
    item = get(request_id)
    return _hydrate(item) if item else None


def list_by_hip(hip_id: str, status: Optional[str] = None, limit: int = 50) -> list:
    """All transactions for a hospital (newest first), optionally filtered by status."""
    kwargs = {
        "IndexName": "hip_id-index",
        "KeyConditionExpression": Key("hip_id").eq(hip_id),
        "ScanIndexForward": False,   # newest first by created_at
        "Limit": limit,
    }
    if status:
        kwargs["FilterExpression"] = Attr("status").eq(status)
    resp = config.abdm_transactions_table.query(**kwargs)
    return [_hydrate(i) for i in resp.get("Items", [])]


def list_recent(status: Optional[str] = None, limit: int = 50) -> list:
    """All recent transactions (admin fallback when no hip_id given). Scan-based."""
    kwargs: dict = {"Limit": limit}
    if status:
        kwargs["FilterExpression"] = Attr("status").eq(status)
    resp = config.abdm_transactions_table.scan(**kwargs)
    items = resp.get("Items", [])
    items.sort(key=lambda i: i.get("created_at", ""), reverse=True)
    return [_hydrate(i) for i in items]


def count_recent_for_hip(hip_id: str, api: str, since: datetime) -> int:
    """
    Count transactions of a given api for a hospital created since `since`.
    Useful for a rate-limit guard (e.g. ABDM blocks >3 generate-token calls/hour).
    """
    resp = config.abdm_transactions_table.query(
        IndexName="hip_id-index",
        KeyConditionExpression=Key("hip_id").eq(hip_id) & Key("created_at").gte(since.isoformat()),
        FilterExpression=Attr("api").eq(api),
        Select="COUNT",
    )
    return resp.get("Count", 0)


def _hydrate(item: dict) -> dict:
    """Parse JSON-string payload fields back into objects for API responses."""
    out = dict(item)
    for f in ("request_payload", "callback_payload", "error"):
        if f in out:
            out[f] = _load(out[f])
    return out
