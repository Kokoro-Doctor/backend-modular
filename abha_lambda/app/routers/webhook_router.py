"""
ABDM → Kokoro inbound webhook callbacks (Milestone 2+).

ABDM POSTs async responses to these exact paths after we register our base URL
via 3.2.4. The paths are fixed by the ABDM spec and cannot be changed.

Endpoints:
  POST /api/v3/hip/token/on-generate-token  — 4.3.2: receive link token
  POST /api/v3/link/on_carecontext          — 4.3.4: care context linking confirmation

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

from app.abdm.schemas import CareContextCallbackPayload, LinkTokenCallbackPayload
from app.services import abha_accounts_service, abdm_transactions_service, hospital_abdm_service
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
