"""
ABDM HIU/gateway HTTP client (Milestone 3).

Used for all HIU-role calls to the ABDM gateway:
  - Consent request init            (4.3.1)
  - Consent notify acknowledgement  (4.3.4)
  - Consent request status          (4.3.5)
  - Consent artefact fetch          (4.3.7)
  - HIU health-information request  (data-flow/v3/health-information/request)
  - Health-information notify (HIU)  (6.3.6 — sessionStatus RECEIVED)

Every HIU request carries X-HIU-ID — the per-hospital ABDM service ID, looked
up from HospitalAbdmConfig. Callers must always pass hiu_id explicitly; there
is no global default. Mirrors abdm/hip_client.py, which does the same for HIP.
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


def _gateway_headers(hiu_id: str, request_id: Optional[str] = None) -> dict:
    """Build headers for HIU gateway calls. request_id controls REQUEST-ID for callback correlation."""
    return {
        "Content-Type":  "application/json",
        "REQUEST-ID":    request_id or str(uuid.uuid4()),
        "TIMESTAMP":     _utc_timestamp(),
        "Authorization": f"Bearer {token_manager.get_access_token()}",
        "X-CM-ID":       config.ABDM_X_CM_ID,
        "X-HIU-ID":      hiu_id,
    }


def post(
    path: str,
    payload: Any,
    hiu_id: str,
    request_id: Optional[str] = None,
) -> dict:
    """
    POST to the ABDM gateway base URL with a per-hospital X-HIU-ID header.
    Pass request_id to control the REQUEST-ID header (async callback correlation).
    """
    url = f"{config.ABDM_GATEWAY_BASE_URL}{path}"
    headers = _gateway_headers(hiu_id=hiu_id, request_id=request_id)
    logger.debug("[HIUClient] POST %s hiu_id=%s request_id=%s", path, hiu_id, headers["REQUEST-ID"])
    resp = requests.post(url, json=payload, headers=headers, timeout=20)
    return _handle_response(resp, path)


def _handle_response(resp: requests.Response, path: str) -> dict:
    if resp.ok:
        return resp.json() if resp.content else {}
    _raise_error(resp, path)


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

    logger.error("[HIUClient] Error %s on %s — code=%s message=%s", resp.status_code, path, code, message)
    status = resp.status_code if resp.status_code in (400, 401, 403, 404, 409, 422, 429) else 502
    raise HTTPException(status_code=status, detail=f"ABDM error [{code}]: {message}")
