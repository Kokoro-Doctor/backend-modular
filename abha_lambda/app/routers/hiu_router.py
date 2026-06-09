"""
HIU endpoints (Milestone 3) — Kokoro → ABDM outbound calls on a hospital's behalf.

All HIU calls are async: they return 202-style acceptance immediately and ABDM
posts results to our registered webhook (/api/v3/hiu/...). See webhook_router.py
for the inbound callback handlers.

Endpoints:
  POST /abha/hiu/consent/request            — 4.3.1: raise a consent request
  GET  /abha/hiu/consent/request/{rid}      — inspect a consent request's state
  POST /abha/hiu/consent/status             — 4.3.5: poll consent request status
  POST /abha/hiu/consent/fetch              — 4.3.7: fetch a granted artefact
  POST /abha/hiu/health-information/request — request the records under a consent
  GET  /abha/hiu/data/{rid}                 — inspect a received data transfer
"""
from fastapi import APIRouter, HTTPException

from app.abdm.schemas import (
    HiuConsentInitRequest,
    HiuConsentStatusRequest,
    HiuConsentFetchRequest,
    HiuHealthInfoRequest,
)
from app.services import hiu_consent_service, hiu_data_service
from app.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/abha/hiu", tags=["HIU (Milestone 3)"])


# ---------------------------------------------------------------------------
# 4.3.1 — Consent request init
# ---------------------------------------------------------------------------

@router.post("/consent/request")
def consent_request(body: HiuConsentInitRequest):
    """Raise a consent request to the patient. consentRequest.id arrives via on-init."""
    try:
        request_id = hiu_consent_service.request_consent_init(
            hospital_id=body.hospital_id,
            patient_abha_address=body.patient_abha_address,
            hi_types=body.hi_types,
            date_from=body.date_from,
            date_to=body.date_to,
            data_erase_at=body.data_erase_at,
            requester_name=body.requester_name,
            requester_id_value=body.requester_id_value,
            requester_id_type=body.requester_id_type,
            requester_id_system=body.requester_id_system,
            purpose_code=body.purpose_code,
            purpose_text=body.purpose_text,
            purpose_ref_uri=body.purpose_ref_uri,
            access_mode=body.access_mode,
            frequency_unit=body.frequency_unit,
            frequency_value=body.frequency_value,
            frequency_repeats=body.frequency_repeats,
            hip_id=body.hip_id,
            care_contexts=body.care_contexts,
        )
        return {
            "message":     "Consent request accepted. consentRequestId will arrive via on-init.",
            "request_id":  request_id,
            "hospital_id": body.hospital_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[HIU] consent_request failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/consent/request/{request_id}")
def get_consent_request(request_id: str):
    """Inspect a consent request's lifecycle state (status, consentRequestId, consentIds)."""
    row = hiu_consent_service.get(request_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"No consent request for request_id {request_id}")
    return {"consent_request": row}


# ---------------------------------------------------------------------------
# 4.3.5 — Consent request status
# ---------------------------------------------------------------------------

@router.post("/consent/status")
def consent_status(body: HiuConsentStatusRequest):
    """Poll ABDM for the consent request status (result via on-status callback)."""
    try:
        hiu_consent_service.request_consent_status(body.hospital_id, body.consent_request_id)
        return {"message": "Status request accepted. Result arrives via on-status callback."}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[HIU] consent_status failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 4.3.7 — Consent artefact fetch
# ---------------------------------------------------------------------------

@router.post("/consent/fetch")
def consent_fetch(body: HiuConsentFetchRequest):
    """Fetch a granted consent artefact (result via on-fetch callback, stored in ConsentArtefacts)."""
    try:
        hiu_consent_service.fetch_consent(body.hospital_id, body.consent_id)
        return {"message": "Fetch request accepted. Artefact arrives via on-fetch callback."}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[HIU] consent_fetch failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Health-information request
# ---------------------------------------------------------------------------

@router.post("/health-information/request")
def health_information_request(body: HiuHealthInfoRequest):
    """Request the patient's records under a granted consent. Records arrive (encrypted) at our dataPushUrl."""
    try:
        request_id = hiu_data_service.request_health_information(
            hospital_id=body.hospital_id,
            consent_id=body.consent_id,
            date_from=body.date_from,
            date_to=body.date_to,
        )
        return {
            "message":     "Health-information request accepted. Records arrive via data push.",
            "request_id":  request_id,
            "hospital_id": body.hospital_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[HIU] health_information_request failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/data/{request_id}")
def get_data_request(request_id: str):
    """Inspect a data request (decrypted bundles included once received). Private key is redacted."""
    row = hiu_data_service.get_redacted(request_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"No data request for request_id {request_id}")
    return {"data_request": row}
