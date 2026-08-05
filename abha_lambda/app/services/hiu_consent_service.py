"""
HIU consent business logic (Milestone 3, Section 4.3) — Kokoro acting as HIU.

Flow (Kokoro requests a patient's records from another HIP):
  4.3.1  init           (us → ABDM)  request_consent_init()
  4.3.2  on-init        (ABDM → us)  handled in webhook_router → record_on_init()
         notify         (ABDM → us)  patient approved/denied/revoked → record_notify()
  4.3.4  hiu/on-notify  (us → ABDM)  acknowledge_consent_notify()
  4.3.5  status         (us → ABDM)  request_consent_status()
  4.3.6  on-status      (ABDM → us)  record_on_status()
  4.3.7  fetch          (us → ABDM)  fetch_consent()
  4.3.8  on-fetch       (ABDM → us)  artefact persisted via consent_service

State lives in the HiuConsentRequests table:
  PK:  request_id              (our REQUEST-ID on the 4.3.1 init call)
  GSI: consent_request_id-index (consentRequest.id assigned by ABDM at on-init)
  GSI: hiu_id-index            (hiu_id HASH, created_at RANGE) — per-hospital listing
  TTL: ttl
"""
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, List, Optional

from boto3.dynamodb.conditions import Key

from app import config
from app.abdm import hiu_client
from app.services import hospital_abdm_service, consent_service
from app.logger import get_logger

logger = get_logger(__name__)

# ABDM gateway paths
_INIT_PATH      = "/api/hiecm/consent/v3/request/init"          # 4.3.1
_ON_NOTIFY_PATH = "/api/hiecm/consent/v3/request/hiu/on-notify" # 4.3.4
_STATUS_PATH    = "/api/hiecm/consent/v3/request/status"        # 4.3.5
_FETCH_PATH     = "/api/hiecm/consent/v3/fetch"                 # 4.3.7

_TTL_DAYS = 180

# Status values for the HiuConsentRequests row
REQUESTED = "REQUESTED"
GRANTED   = "GRANTED"
DENIED    = "DENIED"
REVOKED   = "REVOKED"
EXPIRED   = "EXPIRED"


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
# 4.3.1 — Init a consent request
# ---------------------------------------------------------------------------

def request_consent_init(
    hospital_id: str,
    patient_abha_address: str,
    hi_types: List[str],
    date_from: str,
    date_to: str,
    data_erase_at: str,
    requester_name: str,
    requester_id_value: str,
    requester_id_type: str = "REGNO",
    requester_id_system: str = "https://www.mciindia.org",
    purpose_code: str = "CAREMGT",
    purpose_text: str = "Care Management",
    purpose_ref_uri: str = "www.abdm.gov.in",
    access_mode: str = "VIEW",
    frequency_unit: str = "HOUR",
    frequency_value: int = 1,
    frequency_repeats: int = 0,
    hip_id: Optional[str] = None,
    care_contexts: Optional[List[dict]] = None,
) -> str:
    """
    Ask ABDM to raise a consent request to the patient on this hospital's behalf.
    Returns immediately (202). The consentRequest.id arrives via the 4.3.2 on-init
    callback. Returns our request_id (REQUEST-ID) for tracking.
    """
    hospital = hospital_abdm_service.get_or_raise(hospital_id)
    hiu_id   = hospital_abdm_service.resolve_hiu_id(hospital)
    request_id = str(uuid.uuid4())

    consent: dict = {
        "hiu":     {"id": hiu_id},
        "patient": {"id": patient_abha_address},
        "hiTypes": hi_types,
        "purpose": {"code": purpose_code, "text": purpose_text, "refUri": purpose_ref_uri},
        "requester": {
            "name": requester_name,
            "identifier": {
                "type":   requester_id_type,
                "value":  requester_id_value,
                "system": requester_id_system,
            },
        },
        "permission": {
            "accessMode": access_mode,
            "dateRange":  {"from": date_from, "to": date_to},
            "dataEraseAt": data_erase_at,
            "frequency":  {"unit": frequency_unit, "value": frequency_value, "repeats": frequency_repeats},
        },
    }
    if hip_id:
        consent["hip"] = {"id": hip_id}
    if care_contexts:
        consent["careContexts"] = care_contexts

    _save_init(
        request_id=request_id,
        hospital_id=hospital_id,
        hiu_id=hiu_id,
        patient=patient_abha_address,
        hi_types=hi_types,
        request_payload=consent,
    )

    logger.info(
        "[HiuConsentService] init consent request hospital_id=%s hiu_id=%s patient=%s request_id=%s",
        hospital_id, hiu_id, patient_abha_address, request_id,
    )
    hiu_client.post(_INIT_PATH, {"consent": consent}, hiu_id=hiu_id, request_id=request_id)
    return request_id


# ---------------------------------------------------------------------------
# 4.3.4 — Acknowledge a consent notify (us → ABDM)
# ---------------------------------------------------------------------------

def acknowledge_consent_notify(
    consent_ids: List[str],
    notify_request_id: str,
    hiu_id: str,
) -> None:
    """Respond to the patient-approval/deny/revoke notify, echoing its REQUEST-ID back."""
    payload = {
        "acknowledgement": [{"status": "OK", "consentId": cid} for cid in consent_ids],
        "response": {"requestId": notify_request_id},
    }
    logger.info("[HiuConsentService] ack consent notify consent_ids=%s hiu_id=%s", consent_ids, hiu_id)
    hiu_client.post(_ON_NOTIFY_PATH, payload, hiu_id=hiu_id, request_id=str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# 4.3.5 — Poll consent request status
# ---------------------------------------------------------------------------

def request_consent_status(hospital_id: str, consent_request_id: str) -> None:
    """Ask ABDM for the current status of a consent request (result via 4.3.6 callback)."""
    hospital = hospital_abdm_service.get_or_raise(hospital_id)
    hiu_id   = hospital_abdm_service.resolve_hiu_id(hospital)
    logger.info("[HiuConsentService] status check consent_request_id=%s hiu_id=%s", consent_request_id, hiu_id)
    hiu_client.post(
        _STATUS_PATH,
        {"consentRequestId": consent_request_id},
        hiu_id=hiu_id,
        request_id=str(uuid.uuid4()),
    )


# ---------------------------------------------------------------------------
# 4.3.7 — Fetch a granted consent artefact
# ---------------------------------------------------------------------------

def fetch_consent(hospital_id: str, consent_id: str) -> None:
    """Request the full consent artefact (result via 4.3.8 on-fetch callback)."""
    hospital = hospital_abdm_service.get_or_raise(hospital_id)
    hiu_id   = hospital_abdm_service.resolve_hiu_id(hospital)
    logger.info("[HiuConsentService] fetch consent_id=%s hiu_id=%s", consent_id, hiu_id)
    hiu_client.post(_FETCH_PATH, {"consentId": consent_id}, hiu_id=hiu_id, request_id=str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# Callback recorders (called from webhook_router)
# ---------------------------------------------------------------------------

def record_on_init(request_id: str, consent_request_id: Optional[str], error: Any) -> None:
    """4.3.2 — store the consentRequest.id ABDM assigned (correlated by our request_id)."""
    fields = {"updated_at": _now().isoformat()}
    if consent_request_id:
        fields["consent_request_id"] = consent_request_id
    if error:
        fields["status"] = "FAILED"
        fields["error"] = _dump(error)
    _update(request_id, fields)


def record_notify(consent_request_id: Optional[str], status: str, consent_ids: List[str]) -> Optional[dict]:
    """
    Patient approved/denied/revoked. Correlated by consent_request_id (via GSI).
    Stores the granted consentId(s) + status. Returns the row (so the caller can
    resolve hiu_id for the 4.3.4 ack), or None if we have no matching request.
    """
    row = get_by_consent_request_id(consent_request_id) if consent_request_id else None
    if not row:
        logger.warning("[HiuConsentService] notify for unknown consent_request_id=%s", consent_request_id)
        return None
    fields = {
        "status":      status or REQUESTED,
        "consent_ids": consent_ids,
        "updated_at":  _now().isoformat(),
    }
    _update(row["request_id"], fields)
    return row


def record_on_status(consent_request_id: Optional[str], status: Optional[str]) -> None:
    """4.3.6 — update the row status from a status poll."""
    row = get_by_consent_request_id(consent_request_id) if consent_request_id else None
    if not row:
        logger.warning("[HiuConsentService] on-status for unknown consent_request_id=%s", consent_request_id)
        return
    _update(row["request_id"], {"status": status or REQUESTED, "updated_at": _now().isoformat()})


def record_on_fetch(consent_id: str, status: str, consent_detail: Optional[dict], signature: Optional[str], hiu_id: Optional[str]) -> None:
    """
    4.3.8 — persist the fetched artefact. Reuses the ConsentArtefacts table so the
    HIU side and the HIP side share one artefact store.
    """
    consent_service.save(
        consent_id=consent_id,
        status=status or GRANTED,
        consent_detail=consent_detail,
        hip_id=None,
        notify_request_id=None,
        signature=signature,
    )
    logger.info("[HiuConsentService] stored fetched artefact consent_id=%s status=%s", consent_id, status)


# ---------------------------------------------------------------------------
# DynamoDB helpers (HiuConsentRequests)
# ---------------------------------------------------------------------------

def _save_init(
    request_id: str,
    hospital_id: str,
    hiu_id: str,
    patient: str,
    hi_types: List[str],
    request_payload: dict,
) -> None:
    now = _now().isoformat()
    item = {
        "request_id":      request_id,
        "hospital_id":     hospital_id,
        "hiu_id":          hiu_id,
        "patient":         patient,
        "hi_types":        hi_types,
        "status":          REQUESTED,
        "request_payload": _dump(request_payload),
        "created_at":      now,
        "updated_at":      now,
        "ttl":             _ttl_epoch(),
    }
    config.hiu_consent_requests_table.put_item(Item=item)


def _update(request_id: str, fields: dict) -> None:
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        return
    set_parts = [f"#{k} = :{k}" for k in fields]
    names     = {f"#{k}": k for k in fields}
    values    = {f":{k}": v for k, v in fields.items()}
    config.hiu_consent_requests_table.update_item(
        Key={"request_id": request_id},
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def get(request_id: str) -> Optional[dict]:
    resp = config.hiu_consent_requests_table.get_item(Key={"request_id": request_id})
    return resp.get("Item")


def get_by_consent_request_id(consent_request_id: str) -> Optional[dict]:
    resp = config.hiu_consent_requests_table.query(
        IndexName="consent_request_id-index",
        KeyConditionExpression=Key("consent_request_id").eq(consent_request_id),
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def list_by_hiu(hiu_id: str, limit: int = 50) -> list:
    resp = config.hiu_consent_requests_table.query(
        IndexName="hiu_id-index",
        KeyConditionExpression=Key("hiu_id").eq(hiu_id),
        ScanIndexForward=False,
        Limit=limit,
    )
    return resp.get("Items", [])
