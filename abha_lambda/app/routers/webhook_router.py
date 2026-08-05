"""
ABDM → Kokoro inbound webhook callbacks (Milestone 2+).

ABDM POSTs async responses to these exact paths after we register our base URL
via 3.2.4. The paths are fixed by the ABDM spec and cannot be changed.

Endpoints:
  POST /api/v3/hip/token/on-generate-token     — 4.3.2: receive link token
  POST /api/v3/link/on_carecontext             — 4.3.4: care context linking confirmation
  POST /api/v3/consent/request/hip/notify      — 6.3.1: consent granted/revoked notify
  POST /api/v3/hip/health-information/request   — 6.3.3: health-information request

X-HIP-ID on every callback tells us which hospital the event belongs to.
We use it to look up the hospital record (for logging/routing) and to scope
the link token storage correctly in AbhaAccounts.

Auth: ABDM sends a gateway JWT in the Authorization header on every callback.
We log it for audit. Full cryptographic validation against ABDM's public key
can be added here once the ABDM JWKS endpoint is confirmed.
"""
import jwt as pyjwt
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Header

from app.abdm.schemas import (
    CareContextCallbackPayload,
    LinkTokenCallbackPayload,
    ConsentNotificationPayload,
    HealthInformationRequestPayload,
    HiuConsentOnInitPayload,
    HiuConsentNotifyPayload,
    HiuConsentOnStatusPayload,
    HiuConsentOnFetchPayload,
    HiuDataTransferPayload,
)
from app.services import (
    abha_accounts_service,
    abdm_transactions_service,
    hospital_abdm_service,
    consent_service,
    data_flow_service,
    hiu_consent_service,
    hiu_data_service,
)
from app.logger import get_logger

logger = get_logger(__name__)

# No prefix — paths must match ABDM spec exactly
router = APIRouter(tags=["ABDM Webhooks"])


def _correlation_request_id(response) -> Optional[str]:
    """
    Pull the original REQUEST-ID back out of the callback body.
    ABDM echoes it as response.requestId — this is what links the callback to
    the PENDING transaction we recorded when we sent the request. (Note: the
    REQUEST-ID *header* on the callback is a different id for the callback's
    own transaction, so we must correlate on the body, not the header.)
    """
    if isinstance(response, dict):
        return response.get("requestId")
    return None


def _parse_link_token_expiry(link_token: str) -> str:
    """
    Decode the link token JWT (without signature verification) to extract
    the expiry claim. Falls back to 7 days from now if the claim is missing.
    """
    try:
        payload = pyjwt.decode(
            link_token,
            options={"verify_signature": False},
            algorithms=["RS256", "RS512", "HS256"],
        )
        exp = payload.get("exp")
        if exp:
            return datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()
    except Exception:
        logger.warning("[Webhook] Could not decode link token expiry, using 7-day default")
    return (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()


# ---------------------------------------------------------------------------
# 4.3.2 — Link token callback
# ---------------------------------------------------------------------------

@router.post("/api/v3/hip/token/on-generate-token", status_code=202)
def on_generate_token(
    body: LinkTokenCallbackPayload,
    authorization: Optional[str] = Header(None),
    x_hip_id: Optional[str] = Header(None, alias="X-HIP-ID"),
    request_id: Optional[str] = Header(None, alias="REQUEST-ID"),
):
    """
    ABDM posts the link token here after a successful 4.3.1 request.
    X-HIP-ID identifies which hospital this token belongs to.
    We store the token in AbhaAccounts scoped to that hip_id.
    """
    corr_id = _correlation_request_id(body.response)
    logger.info(
        "[ABDM-CB][on-generate-token] correlation_request_id=%s callback_header_request_id=%s "
        "abha_address=%s x_hip_id=%s link_token_received=%s",
        corr_id, request_id, body.abhaAddress, x_hip_id, bool(body.linkToken),
    )

    if not x_hip_id:
        logger.warning(
            "[ABDM-CB][on-generate-token] missing X-HIP-ID for abha_address=%s — cannot scope token",
            body.abhaAddress,
        )
        if corr_id:
            abdm_transactions_service.mark_failed(
                corr_id,
                error={"message": "Callback missing X-HIP-ID; token not scoped"},
                callback_payload=body.model_dump(),
            )
        return {}

    expiry = _parse_link_token_expiry(body.linkToken)

    db_ok = True
    try:
        abha_accounts_service.save_link_token(
            abha_address=body.abhaAddress,
            link_token=body.linkToken,
            link_token_expiry=expiry,
            hip_id=x_hip_id,
        )
    except Exception:
        db_ok = False
        # Log but still return 202 — ABDM must not retry because of our DB error
        logger.exception(
            "[ABDM-CB][on-generate-token] DB save failed for abha_address=%s hip_id=%s",
            body.abhaAddress, x_hip_id,
        )

    # Close the transaction either way so the row reflects what happened.
    if corr_id:
        if db_ok:
            abdm_transactions_service.mark_completed(corr_id, callback_payload=body.model_dump())
        else:
            abdm_transactions_service.mark_failed(
                corr_id,
                error={"message": "Link token received but DB save failed"},
                callback_payload=body.model_dump(),
            )

    return {}


# ---------------------------------------------------------------------------
# 4.3.4 — Care context linking callback
# ---------------------------------------------------------------------------

@router.post("/api/v3/link/on_carecontext", status_code=202)
def on_care_context(
    body: CareContextCallbackPayload,
    authorization: Optional[str] = Header(None),
    x_hip_id: Optional[str] = Header(None, alias="X-HIP-ID"),
    request_id: Optional[str] = Header(None, alias="REQUEST-ID"),
):
    """
    ABDM posts the care context linking result here after a 4.3.3 request.
    X-HIP-ID identifies which hospital's care context was linked.
    Success and error cases are both handled — we log the outcome.

    Extend this handler to update appointment/health record status once
    that domain model is in place (Milestone 3+).
    """
    corr_id = _correlation_request_id(body.response)

    # Resolve hospital name for richer logging
    hospital_name = None
    if x_hip_id:
        try:
            hospital = hospital_abdm_service.get_by_hip_id(x_hip_id)
            hospital_name = hospital.get("facility_name") if hospital else None
        except Exception:
            pass  # non-fatal — logging only

    if body.error:
        logger.error(
            "[ABDM-CB][on_carecontext] FAILED correlation_request_id=%s abha_address=%s "
            "x_hip_id=%s hospital=%s error=%s",
            corr_id, body.abhaAddress, x_hip_id, hospital_name, body.error,
        )
        if corr_id:
            abdm_transactions_service.mark_failed(
                corr_id, error=body.error, callback_payload=body.model_dump()
            )
    else:
        logger.info(
            "[ABDM-CB][on_carecontext] SUCCESS correlation_request_id=%s abha_address=%s "
            "x_hip_id=%s hospital=%s status=%s",
            corr_id, body.abhaAddress, x_hip_id, hospital_name, body.status,
        )
        if corr_id:
            abdm_transactions_service.mark_completed(corr_id, callback_payload=body.model_dump())

    # Future: update care context / appointment status on the health record here

    return {}


# ---------------------------------------------------------------------------
# 6.3.1 — Consent notify (ABDM → HIP when a consent is granted/revoked)
# ---------------------------------------------------------------------------

@router.post("/api/v3/consent/request/hip/notify", status_code=202)
def on_consent_notify(
    body: ConsentNotificationPayload,
    authorization: Optional[str] = Header(None),
    x_hip_id: Optional[str] = Header(None, alias="X-HIP-ID"),
    request_id: Optional[str] = Header(None, alias="REQUEST-ID"),
):
    """
    ABDM posts the consent artefact here once a consent request is granted
    (or revoked). We persist the artefact keyed by consentId so the later
    6.3.3 health-information request can be served, then acknowledge via 6.3.2,
    echoing this callback's REQUEST-ID back as response.requestId.
    """
    notification = body.notification
    consent_id = notification.consentId
    if not consent_id and isinstance(notification.consentDetail, dict):
        consent_id = notification.consentDetail.get("consentId")

    logger.info(
        "[ABDM-CB][consent-notify] consent_id=%s status=%s x_hip_id=%s request_id=%s",
        consent_id, notification.status, x_hip_id, request_id,
    )

    if not consent_id:
        logger.warning("[ABDM-CB][consent-notify] missing consentId — cannot process")
        return {}

    # Persist the artefact (best-effort — still 202 so ABDM doesn't retry on our DB error).
    try:
        consent_service.save(
            consent_id=consent_id,
            status=notification.status or "GRANTED",
            consent_detail=notification.consentDetail,
            hip_id=x_hip_id,
            notify_request_id=request_id,
            signature=notification.signature,
        )
    except Exception:
        logger.exception("[ABDM-CB][consent-notify] failed to persist consent_id=%s", consent_id)

    # Acknowledge the notify (6.3.2). Needs hip_id + the callback's REQUEST-ID.
    if x_hip_id and request_id:
        try:
            data_flow_service.acknowledge_consent_notify(
                consent_id=consent_id,
                notify_request_id=request_id,
                hip_id=x_hip_id,
            )
        except Exception:
            logger.exception(
                "[ABDM-CB][consent-notify] on-notify acknowledgement failed consent_id=%s",
                consent_id,
            )
    else:
        logger.warning(
            "[ABDM-CB][consent-notify] missing X-HIP-ID or REQUEST-ID — skipping 6.3.2 ack "
            "(consent_id=%s)", consent_id,
        )

    return {}


# ---------------------------------------------------------------------------
# 6.3.3 — Health-information request (ABDM → HIP, kicks off the data push)
# ---------------------------------------------------------------------------

@router.post("/api/v3/hip/health-information/request", status_code=202)
def on_health_information_request(
    body: HealthInformationRequestPayload,
    authorization: Optional[str] = Header(None),
    x_hip_id: Optional[str] = Header(None, alias="X-HIP-ID"),
    request_id: Optional[str] = Header(None, alias="REQUEST-ID"),
):
    """
    ABDM forwards the HIU's data request here: consent id, the HIU dataPushUrl
    and the HIU's key material. We hand off to data_flow_service which
    acknowledges (6.3.4), encrypts + pushes the bundles (6.3.5) and notifies the
    CM (6.3.6). Wrapped so we always return 202 to ABDM regardless of outcome.
    """
    hi = body.hiRequest
    consent_id = (hi.consent or {}).get("id")
    data_push_url = hi.dataPushUrl
    hiu_key_material = hi.keyMaterial or {}
    # transactionId may arrive in the body or fall back to the REQUEST-ID header.
    transaction_id = body.transactionId or request_id

    logger.info(
        "[ABDM-CB][hi-request] consent_id=%s transaction_id=%s x_hip_id=%s data_push_url=%s",
        consent_id, transaction_id, x_hip_id, data_push_url,
    )

    if not (consent_id and data_push_url and x_hip_id and transaction_id):
        logger.warning(
            "[ABDM-CB][hi-request] missing required field(s) "
            "(consent_id=%s data_push_url=%s x_hip_id=%s transaction_id=%s) — cannot process",
            consent_id, data_push_url, x_hip_id, transaction_id,
        )
        return {}

    try:
        data_flow_service.handle_health_information_request(
            consent_id=consent_id,
            transaction_id=transaction_id,
            hi_request_id=request_id,
            data_push_url=data_push_url,
            hiu_key_material=hiu_key_material,
            hip_id=x_hip_id,
        )
    except Exception:
        logger.exception(
            "[ABDM-CB][hi-request] data flow errored consent_id=%s transaction_id=%s",
            consent_id, transaction_id,
        )

    return {}


# ===========================================================================
# Milestone 3 — HIU inbound callbacks (ABDM → Kokoro as HIU)
# ===========================================================================

def _hiu_correlation_request_id(response) -> Optional[str]:
    """Pull our original REQUEST-ID back out of an HIU callback body (response.requestId)."""
    if isinstance(response, dict):
        return response.get("requestId")
    return None


# ---------------------------------------------------------------------------
# 4.3.2 — Consent request on-init
# ---------------------------------------------------------------------------

@router.post("/api/v3/hiu/consent/request/on-init", status_code=202)
def on_hiu_consent_init(
    body: HiuConsentOnInitPayload,
    authorization: Optional[str] = Header(None),
    x_hiu_id: Optional[str] = Header(None, alias="X-HIU-ID"),
    request_id: Optional[str] = Header(None, alias="REQUEST-ID"),
):
    """ABDM returns the assigned consentRequest.id after a 4.3.1 init. Correlated by response.requestId."""
    corr_id = _hiu_correlation_request_id(body.response)
    consent_request_id = (body.consentRequest or {}).get("id")
    logger.info(
        "[ABDM-CB][hiu-on-init] corr_request_id=%s consent_request_id=%s x_hiu_id=%s error=%s",
        corr_id, consent_request_id, x_hiu_id, body.error,
    )
    if corr_id:
        try:
            hiu_consent_service.record_on_init(corr_id, consent_request_id, body.error)
        except Exception:
            logger.exception("[ABDM-CB][hiu-on-init] failed to record corr_id=%s", corr_id)
    return {}


# ---------------------------------------------------------------------------
# Consent notify — patient approved / denied / revoked
# ---------------------------------------------------------------------------

@router.post("/api/v3/hiu/consent/request/notify", status_code=202)
def on_hiu_consent_notify(
    body: HiuConsentNotifyPayload,
    authorization: Optional[str] = Header(None),
    x_hiu_id: Optional[str] = Header(None, alias="X-HIU-ID"),
    request_id: Optional[str] = Header(None, alias="REQUEST-ID"),
):
    """
    Patient acted on the consent request. We persist the granted consentId(s) and
    acknowledge (4.3.4), echoing this callback's REQUEST-ID back.
    """
    notification = body.notification or {}
    status = notification.get("status")
    consent_request_id = notification.get("consentRequestId") or notification.get("consentRequestid")
    artefacts = notification.get("consentArtefacts") or notification.get("consentArtefact") or []
    consent_ids = [a.get("id") for a in artefacts if isinstance(a, dict) and a.get("id")]

    logger.info(
        "[ABDM-CB][hiu-notify] consent_request_id=%s status=%s consent_ids=%s x_hiu_id=%s",
        consent_request_id, status, consent_ids, x_hiu_id,
    )

    row = None
    try:
        row = hiu_consent_service.record_notify(consent_request_id, status, consent_ids)
    except Exception:
        logger.exception("[ABDM-CB][hiu-notify] failed to record consent_request_id=%s", consent_request_id)

    # Acknowledge (4.3.4). Resolve hiu_id from the stored row, else the header.
    hiu_id = (row or {}).get("hiu_id") or x_hiu_id
    if hiu_id and request_id and consent_ids:
        try:
            hiu_consent_service.acknowledge_consent_notify(
                consent_ids=consent_ids, notify_request_id=request_id, hiu_id=hiu_id
            )
        except Exception:
            logger.exception("[ABDM-CB][hiu-notify] ack failed consent_request_id=%s", consent_request_id)
    else:
        logger.warning(
            "[ABDM-CB][hiu-notify] missing hiu_id/REQUEST-ID/consent_ids — skipping 4.3.4 ack"
        )
    return {}


# ---------------------------------------------------------------------------
# 4.3.6 — Consent request on-status
# ---------------------------------------------------------------------------

@router.post("/api/v3/hiu/consent/request/on-status", status_code=202)
def on_hiu_consent_status(
    body: HiuConsentOnStatusPayload,
    authorization: Optional[str] = Header(None),
    x_hiu_id: Optional[str] = Header(None, alias="X-HIU-ID"),
    request_id: Optional[str] = Header(None, alias="REQUEST-ID"),
):
    """ABDM returns the consent request status after a 4.3.5 poll."""
    cr = body.consentRequest or {}
    consent_request_id = cr.get("id")
    status = cr.get("status")
    logger.info(
        "[ABDM-CB][hiu-on-status] consent_request_id=%s status=%s x_hiu_id=%s",
        consent_request_id, status, x_hiu_id,
    )
    try:
        hiu_consent_service.record_on_status(consent_request_id, status)
    except Exception:
        logger.exception("[ABDM-CB][hiu-on-status] failed to record consent_request_id=%s", consent_request_id)
    return {}


# ---------------------------------------------------------------------------
# 4.3.8 — Consent on-fetch (the granted artefact)
# ---------------------------------------------------------------------------

@router.post("/api/v3/hiu/consent/on-fetch", status_code=202)
def on_hiu_consent_fetch(
    body: HiuConsentOnFetchPayload,
    authorization: Optional[str] = Header(None),
    x_hiu_id: Optional[str] = Header(None, alias="X-HIU-ID"),
    request_id: Optional[str] = Header(None, alias="REQUEST-ID"),
):
    """ABDM returns the full consent artefact after a 4.3.7 fetch — persisted in ConsentArtefacts."""
    consent = body.consent or {}
    detail = consent.get("consentDetail") or {}
    consent_id = detail.get("consentId") or consent.get("consentId")
    status = consent.get("status")
    signature = consent.get("signature")
    logger.info("[ABDM-CB][hiu-on-fetch] consent_id=%s status=%s x_hiu_id=%s", consent_id, status, x_hiu_id)

    if not consent_id:
        logger.warning("[ABDM-CB][hiu-on-fetch] missing consentId — cannot persist")
        return {}
    try:
        hiu_consent_service.record_on_fetch(
            consent_id=consent_id, status=status, consent_detail=detail,
            signature=signature, hiu_id=x_hiu_id,
        )
    except Exception:
        logger.exception("[ABDM-CB][hiu-on-fetch] failed to persist consent_id=%s", consent_id)
    return {}


# ---------------------------------------------------------------------------
# 6.3.5 inbound — a HIP pushes encrypted records to our HIU dataPushUrl
# ---------------------------------------------------------------------------

@router.post("/api/v3/hiu/health-information/transfer", status_code=202)
def on_hiu_data_transfer(
    body: HiuDataTransferPayload,
    authorization: Optional[str] = Header(None),
    x_hiu_id: Optional[str] = Header(None, alias="X-HIU-ID"),
    request_id: Optional[str] = Header(None, alias="REQUEST-ID"),
):
    """
    A HIP pushes encrypted FHIR entries here (the dataPushUrl we sent on our HI
    request). We decrypt with the stored ephemeral private key, persist, and
    notify the CM (6.3.6). Always returns 202 so ABDM/HIP does not retry.
    """
    logger.info(
        "[ABDM-CB][hiu-transfer] transaction_id=%s entries=%s x_hiu_id=%s",
        body.transactionId, len(body.entries or []), x_hiu_id,
    )
    try:
        hiu_data_service.handle_data_transfer(
            transaction_id=body.transactionId,
            entries=body.entries or [],
            hip_key_material=body.keyMaterial or {},
        )
    except Exception:
        logger.exception("[ABDM-CB][hiu-transfer] data transfer errored transaction_id=%s", body.transactionId)
    return {}
