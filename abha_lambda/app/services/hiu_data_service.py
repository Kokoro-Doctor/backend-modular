"""
HIU data-flow business logic (Milestone 3) — Kokoro requesting + receiving records.

Flow:
  request   (us → ABDM)  request_health_information()
              POST /api/hiecm/data-flow/v3/health-information/request
              We generate an ephemeral X25519 key pair, hand ABDM our public key
              + the dataPushUrl, and persist the PRIVATE key so we can decrypt.
  push      (HIP → us)   handle_data_transfer()  ← our dataPushUrl
              Decrypt entries with the stored private key, persist the FHIR, then
  notify    (us → ABDM)  notify CM the transfer was RECEIVED (6.3.6).

State lives in HiuDataRequests:
  PK:  request_id              (our REQUEST-ID on the HI request)
  GSI: transaction_id-index    (ABDM's transactionId — used to match the push)
  TTL: ttl

SECURITY NOTE: the ephemeral private key is stored (base64) so the asynchronous
push can be decrypted. It is single-use and TTL'd, but for production it should
be wrapped with KMS before persistence (see WASA audit H-6).
"""
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, List, Optional

from boto3.dynamodb.conditions import Key, Attr

from app import config
from app.abdm import hiu_client, data_encryption
from app.services import hospital_abdm_service, abdm_transactions_service
from app.logger import get_logger

logger = get_logger(__name__)

_HI_REQUEST_PATH = "/api/hiecm/data-flow/v3/health-information/request"   # HIU → ABDM
_NOTIFY_PATH     = "/api/hiecm/data-flow/v3/health-information/notify"    # HIU → CM (6.3.6)

_TTL_DAYS = 30

PENDING  = "PENDING"
RECEIVED = "RECEIVED"
FAILED   = "FAILED"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ttl_epoch() -> int:
    return int((_now() + timedelta(days=_TTL_DAYS)).timestamp())


def _dump(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


# ---------------------------------------------------------------------------
# Request health information (us → ABDM)
# ---------------------------------------------------------------------------

def request_health_information(
    hospital_id: str,
    consent_id: str,
    date_from: str,
    date_to: str,
) -> str:
    """
    Ask ABDM (HIU role) for the patient's records under a granted consent.
    Generates our ECDH key pair, sends our public key + dataPushUrl, and persists
    the private key keyed by our request_id so the inbound push can be decrypted.
    Returns request_id.
    """
    hospital = hospital_abdm_service.get_or_raise(hospital_id)
    hiu_id   = hospital_abdm_service.resolve_hiu_id(hospital)
    request_id = str(uuid.uuid4())

    if not config.KOKORO_WEBHOOK_BASE_URL:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400,
            detail="KOKORO_WEBHOOK_BASE_URL is not configured — cannot build the HIU dataPushUrl.",
        )
    data_push_url = config.KOKORO_WEBHOOK_BASE_URL.rstrip("/") + config.KOKORO_HIU_DATA_PUSH_PATH

    # Our ephemeral ECDH material — keep the private key to decrypt the push.
    private_key_b64, nonce_b64, key_material = data_encryption.generate_key_material()

    payload = {
        "hiRequest": {
            "consent":     {"id": consent_id},
            "dateRange":   {"from": date_from, "to": date_to},
            "dataPushUrl": data_push_url,
            "keyMaterial": key_material,
        }
    }

    _save_request(
        request_id=request_id,
        hospital_id=hospital_id,
        hiu_id=hiu_id,
        consent_id=consent_id,
        private_key_b64=private_key_b64,
        nonce_b64=nonce_b64,
    )
    abdm_transactions_service.create_pending(
        request_id=request_id,
        api="hiu-hi-request",
        hospital_id=hospital_id,
        hip_id=hiu_id,
        abha_address=None,
        request_payload={"consent_id": consent_id, "data_push_url": data_push_url},
    )

    logger.info(
        "[HiuDataService] HI request hospital_id=%s hiu_id=%s consent_id=%s request_id=%s",
        hospital_id, hiu_id, consent_id, request_id,
    )
    try:
        resp = hiu_client.post(_HI_REQUEST_PATH, payload, hiu_id=hiu_id, request_id=request_id)
    except Exception as e:
        abdm_transactions_service.mark_failed(request_id, error={"message": str(e)})
        _update(request_id, {"status": FAILED, "error": _dump({"message": str(e)})})
        raise

    # ABDM may return a transactionId synchronously — capture it so the inbound
    # push (which only carries transactionId) can be matched to this request.
    transaction_id = None
    if isinstance(resp, dict):
        transaction_id = resp.get("transactionId") or (resp.get("hiRequest") or {}).get("transactionId")
    if transaction_id:
        _update(request_id, {"transaction_id": transaction_id})

    return request_id


# ---------------------------------------------------------------------------
# Receive pushed data (HIP → us, at our dataPushUrl) + notify CM
# ---------------------------------------------------------------------------

def handle_data_transfer(
    transaction_id: Optional[str],
    entries: List[dict],
    hip_key_material: dict,
) -> None:
    """
    Decrypt records a HIP pushed to our dataPushUrl, persist them, and notify the
    CM that the transfer was RECEIVED (6.3.6). Matches the push to our original
    request by transactionId (falls back to the most recent PENDING request).
    """
    row = _find_request_for_transfer(transaction_id)
    if not row:
        logger.error(
            "[HiuDataService] no matching HI request for transaction_id=%s — cannot decrypt", transaction_id
        )
        return

    request_id = row["request_id"]
    hiu_id     = row.get("hiu_id")
    consent_id = row.get("consent_id")

    try:
        decrypted = data_encryption.decrypt_entries(
            entries=entries or [],
            hip_key_material=hip_key_material,
            receiver_private_key_b64=row["private_key"],
            receiver_nonce_b64=row["nonce"],
        )
        care_context_refs = [d["careContextReference"] for d in decrypted if d.get("careContextReference")]

        # Persist the decrypted FHIR on the row. PRODUCTION: write these into the
        # patient's clinical record store instead of (or in addition to) here.
        _update(request_id, {
            "status":            RECEIVED,
            "received_bundles":  _dump(decrypted),
            "transaction_id":    transaction_id or row.get("transaction_id"),
            "received_at":       _now().isoformat(),
        })
        abdm_transactions_service.mark_completed(
            request_id, callback_payload={"transaction_id": transaction_id, "entries": len(decrypted)}
        )

        # 6.3.6 — tell the CM we received the data.
        _notify_received(
            consent_id=consent_id,
            transaction_id=transaction_id or row.get("transaction_id") or request_id,
            hiu_id=hiu_id,
            care_context_references=care_context_refs,
            session_status="RECEIVED",
            hi_status="OK",
        )
        logger.info("[HiuDataService] transfer RECEIVED request_id=%s entries=%d", request_id, len(decrypted))

    except Exception as e:
        logger.exception("[HiuDataService] decrypt/persist failed request_id=%s", request_id)
        _update(request_id, {"status": FAILED, "error": _dump({"message": str(e)})})
        abdm_transactions_service.mark_failed(request_id, error={"message": str(e)})
        try:
            _notify_received(
                consent_id=consent_id,
                transaction_id=transaction_id or request_id,
                hiu_id=hiu_id,
                care_context_references=[],
                session_status="FAILED",
                hi_status="ERRORED",
                description=str(e)[:200],
            )
        except Exception:
            logger.exception("[HiuDataService] FAILED notify also errored")


def _notify_received(
    consent_id: Optional[str],
    transaction_id: str,
    hiu_id: Optional[str],
    care_context_references: List[str],
    session_status: str = "RECEIVED",
    hi_status: str = "OK",
    description: str = "",
) -> None:
    """6.3.6 — HIU notifies the CM of receipt. notifier.type = HIU."""
    status_responses = [
        {"careContextReference": ref, "hiStatus": hi_status, "description": description}
        for ref in care_context_references
    ] or [{"hiStatus": hi_status, "description": description}]
    payload = {
        "notification": {
            "consentId":     consent_id,
            "transactionId": transaction_id,
            "doneAt":        _now().strftime("%Y-%m-%dT%H:%M:%S.") + f"{_now().microsecond // 1000:03d}Z",
            "notifier":      {"type": "HIU", "id": hiu_id},
            "statusNotification": {
                "sessionStatus":   session_status,
                "hipId":           hiu_id,
                "statusResponses": status_responses,
            },
        },
    }
    hiu_client.post(_NOTIFY_PATH, payload, hiu_id=hiu_id, request_id=str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# DynamoDB helpers (HiuDataRequests)
# ---------------------------------------------------------------------------

def _save_request(
    request_id: str,
    hospital_id: str,
    hiu_id: str,
    consent_id: str,
    private_key_b64: str,
    nonce_b64: str,
) -> None:
    now = _now().isoformat()
    config.hiu_data_requests_table.put_item(Item={
        "request_id":  request_id,
        "hospital_id": hospital_id,
        "hiu_id":      hiu_id,
        "consent_id":  consent_id,
        "private_key": private_key_b64,   # TODO: wrap with KMS before storing (WASA H-6)
        "nonce":       nonce_b64,
        "status":      PENDING,
        "created_at":  now,
        "updated_at":  now,
        "ttl":         _ttl_epoch(),
    })


def _update(request_id: str, fields: dict) -> None:
    fields = {k: v for k, v in fields.items() if v is not None}
    fields["updated_at"] = _now().isoformat()
    set_parts = [f"#{k} = :{k}" for k in fields]
    names     = {f"#{k}": k for k in fields}
    values    = {f":{k}": v for k, v in fields.items()}
    config.hiu_data_requests_table.update_item(
        Key={"request_id": request_id},
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def get(request_id: str) -> Optional[dict]:
    resp = config.hiu_data_requests_table.get_item(Key={"request_id": request_id})
    return resp.get("Item")


def get_redacted(request_id: str) -> Optional[dict]:
    """Like get() but without the private key, safe to return from the admin API."""
    item = get(request_id)
    if not item:
        return None
    out = dict(item)
    out.pop("private_key", None)
    out.pop("nonce", None)
    if "received_bundles" in out:
        try:
            out["received_bundles"] = json.loads(out["received_bundles"])
        except (TypeError, ValueError):
            pass
    return out


def _find_request_for_transfer(transaction_id: Optional[str]) -> Optional[dict]:
    """Match an inbound push to its originating request by transactionId, else latest PENDING."""
    if transaction_id:
        # try transaction_id GSI first
        try:
            resp = config.hiu_data_requests_table.query(
                IndexName="transaction_id-index",
                KeyConditionExpression=Key("transaction_id").eq(transaction_id),
                Limit=1,
            )
            items = resp.get("Items", [])
            if items:
                return items[0]
        except Exception:
            logger.debug("[HiuDataService] transaction_id-index query failed; scanning for PENDING")
        # the request row may equal the transactionId (some sandbox flows reuse it)
        direct = get(transaction_id)
        if direct:
            return direct

    # Fallback: most recent PENDING request (sandbox-friendly; flagged for hardening)
    resp = config.hiu_data_requests_table.scan(
        FilterExpression=Attr("status").eq(PENDING), Limit=25
    )
    items = resp.get("Items", [])
    if not items:
        return None
    items.sort(key=lambda i: i.get("created_at", ""), reverse=True)
    logger.warning(
        "[HiuDataService] matched transfer to latest PENDING request_id=%s (transaction_id=%s)",
        items[0]["request_id"], transaction_id,
    )
    return items[0]
