"""
ABDM HIP/gateway HTTP client.

Used for all Milestone 2 APIs that hit the ABDM gateway (not the ABHA base URL):
  - Bridge URL registration (3.2.4)
  - Facility registration (3.2.5)
  - Link token generation (4.3.1)
  - Care context linking (4.3.3)

Unlike abdm/client.py (ABHA identity APIs), every HIP request carries
X-HIP-ID — which is per-hospital, looked up from HospitalAbdmConfig.
Callers must always pass hip_id explicitly; there is no global default.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import requests
from fastapi import HTTPException

from app import config
from app.abdm import token_manager
from app.logger import get_logger

logger = get_logger(__name__)


def _utc_timestamp() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _gateway_headers(
    hip_id: str,
    link_token: Optional[str] = None,
    request_id: Optional[str] = None,
) -> dict:
    """
    Build headers for HIP gateway calls.
    hip_id is the ABDM service ID for the specific hospital making the call.
    request_id is the REQUEST-ID transaction header; callers that need to
    correlate the async callback pass their own (so they can persist it first).
    """
    headers = {
        "Content-Type": "application/json",
        "REQUEST-ID":   request_id or str(uuid.uuid4()),
        "TIMESTAMP":    _utc_timestamp(),
        "Authorization": f"Bearer {token_manager.get_access_token()}",
        "X-CM-ID":      config.ABDM_X_CM_ID,
        "X-HIP-ID":     hip_id,
    }
    if link_token:
        headers["X-LINK-TOKEN"] = link_token
    return headers


def post(
    path: str,
    payload: Any,
    hip_id: str,
    link_token: Optional[str] = None,
    request_id: Optional[str] = None,
) -> dict:
    """
    POST to ABDM gateway base URL with per-hospital X-HIP-ID header.
    Pass request_id to control the REQUEST-ID header (used for async callback
    correlation); if omitted a fresh UUID is generated.
    """
    url = f"{config.ABDM_GATEWAY_BASE_URL}{path}"
    headers = _gateway_headers(hip_id=hip_id, link_token=link_token, request_id=request_id)
    logger.debug("[HIPClient] POST %s hip_id=%s request_id=%s", path, hip_id, headers["REQUEST-ID"])
    resp = requests.post(url, json=payload, headers=headers, timeout=20)
    return _handle_response(resp, path)


def patch(path: str, payload: Any) -> dict:
    """
    PATCH to ABDM gateway base URL.
    Used for bridge URL registration (3.2.4) — does not need X-HIP-ID per spec.
    """
    url = f"{config.ABDM_GATEWAY_BASE_URL}{path}"
    headers = {
        "Content-Type":  "application/json",
        "REQUEST-ID":    str(uuid.uuid4()),
        "TIMESTAMP":     _utc_timestamp(),
        "Authorization": f"Bearer {token_manager.get_access_token()}",
        "X-CM-ID":       config.ABDM_X_CM_ID,
    }
    logger.debug("[HIPClient] PATCH %s", path)
    resp = requests.patch(url, json=payload, headers=headers, timeout=20)
    return _handle_response(resp, path)


def post_facility(path: str, payload: Any, hip_id: str) -> dict:
    """POST to the facility registration host (3.2.5 — different base URL)."""
    url = f"{config.ABDM_FACILITY_REG_BASE_URL}{path}"
    headers = _gateway_headers(hip_id=hip_id)
    logger.debug("[HIPClient] POST (facility) %s hip_id=%s", url, hip_id)
    resp = requests.post(url, json=payload, headers=headers, timeout=20)
    return _handle_response(resp, path)


def get_gateway(path: str) -> dict:
    """GET from the ABDM gateway host (3.2.6 bridge-service lookup, 3.2.7 bridge-services list)."""
    url = f"{config.ABDM_GATEWAY_BASE_URL}{path}"
    headers = {
        "Content-Type":  "application/json",
        "REQUEST-ID":    str(uuid.uuid4()),
        "TIMESTAMP":     _utc_timestamp(),
        "Authorization": f"Bearer {token_manager.get_access_token()}",
        "X-CM-ID":       config.ABDM_X_CM_ID,
    }
    logger.debug("[HIPClient] GET (gateway) %s", url)
    resp = requests.get(url, headers=headers, timeout=20)
    return _handle_response(resp, path)


def post_to_url(
    url: str,
    payload: Any,
    hip_id: str,
    request_id: Optional[str] = None,
) -> dict:
    """
    POST to a fully-qualified absolute URL rather than the ABDM gateway base.

    Used for the data push (6.3.5): ABDM forwards the HIU's `dataPushUrl` in the
    6.3.3 request and the HIP pushes the encrypted bundle straight to that URL.
    Carries the standard ABDM auth/transaction headers + X-HIP-ID.
    """
    headers = _gateway_headers(hip_id=hip_id, request_id=request_id)
    logger.debug("[HIPClient] POST (data-push) %s hip_id=%s request_id=%s",
                 url, hip_id, headers["REQUEST-ID"])
    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    return _handle_response(resp, url)


def _handle_response(resp: requests.Response, path: str) -> dict:
    if not resp.ok:
        _raise_error(resp, path)

    body = resp.json() if resp.content else {}
    embedded_error = _extract_embedded_error(body)
    if embedded_error:
        code, message = embedded_error
        logger.error(
            "[HIPClient] Embedded error in %s response on %s — code=%s message=%s",
            resp.status_code, path, code, message,
        )
        raise HTTPException(status_code=422, detail=f"ABDM error [{code}]: {message}")
    return body


def _extract_embedded_error(body: Any) -> Optional[tuple]:
    """
    Some ABDM endpoints (e.g. MutipleHRPAddUpdateServices) return HTTP 200
    even on failure, with the error embedded in the body instead of the
    status code — either as a bare {"error": {...}} or a list containing
    one, e.g. [{"error": {"code": "2500", "message": "..."}}].
    Returns (code, message) if such an error is found, else None.
    """
    items = body if isinstance(body, list) else [body]
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("error"), dict):
            error = item["error"]
            return str(error.get("code", "unknown")), error.get("message", "Unknown ABDM error")
    return None


def _raise_error(resp: requests.Response, path: str):
    try:
        body = resp.json()
        details = body.get("details", [{}])
        if isinstance(details, list) and details:
            message = details[0].get("message", resp.text)
            code    = details[0].get("code", str(resp.status_code))
        else:
            message = body.get("message", resp.text)
            code    = body.get("code", str(resp.status_code))
    except Exception:
        message = resp.text
        code    = str(resp.status_code)

    logger.error(
        "[HIPClient] Error %s on %s — code=%s message=%s",
        resp.status_code, path, code, message,
    )
    status = resp.status_code if resp.status_code in (400, 401, 403, 404, 409, 422, 429) else 502
    raise HTTPException(status_code=status, detail=f"ABDM error [{code}]: {message}")
