"""
Generic ABDM HTTP client.

Automatically injects the required ABDM headers on every request:
  - REQUEST-ID  (UUID4)
  - TIMESTAMP   (UTC ISO8601 with milliseconds)
  - Authorization: Bearer <gateway access token>

Pass user_token to also include the X-Token header for user-scoped endpoints
(profile/account, abha-card).
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


def _base_headers(user_token: Optional[str] = None) -> dict:
    headers = {
        "Content-Type": "application/json",
        "REQUEST-ID": str(uuid.uuid4()),
        "TIMESTAMP": _utc_timestamp(),
        "Authorization": f"Bearer {token_manager.get_access_token()}",
    }
    if user_token:
        headers["X-Token"] = f"Bearer {user_token}"
    return headers


def post(path: str, payload: Any, user_token: Optional[str] = None) -> dict:
    """POST to the ABHA base URL. Raises HTTPException on non-2xx responses."""
    url = f"{config.ABDM_ABHA_BASE_URL}{path}"
    headers = _base_headers(user_token)
    logger.debug("[ABDMClient] POST %s", path)

    resp = requests.post(url, json=payload, headers=headers, timeout=20)
    return _handle_response(resp, path)


def get(path: str, user_token: Optional[str] = None, raw: bool = False):
    """
    GET from the ABHA base URL.

    Set raw=True to receive the raw Response object (used for binary downloads
    like the ABHA card PDF).
    """
    url = f"{config.ABDM_ABHA_BASE_URL}{path}"
    headers = _base_headers(user_token)
    logger.debug("[ABDMClient] GET %s", path)

    resp = requests.get(url, headers=headers, timeout=20)
    if raw:
        if not resp.ok:
            _raise_abdm_error(resp, path)
        return resp
    return _handle_response(resp, path)


def _handle_response(resp: requests.Response, path: str) -> dict:
    if resp.ok:
        if resp.content:
            return resp.json()
        return {}

    _raise_abdm_error(resp, path)


def _raise_abdm_error(resp: requests.Response, path: str):
    try:
        body = resp.json()
        detail = body.get("details", [{}])
        if isinstance(detail, list) and detail:
            message = detail[0].get("message", resp.text)
            code = detail[0].get("code", str(resp.status_code))
        else:
            message = body.get("message", resp.text)
            code = body.get("code", str(resp.status_code))
    except Exception:
        message = resp.text
        code = str(resp.status_code)

    logger.error("[ABDMClient] Error %s on %s — code=%s message=%s", resp.status_code, path, code, message)

    # Map ABDM HTTP status codes to sensible FastAPI equivalents
    status = resp.status_code if resp.status_code in (400, 401, 403, 404, 409, 422, 429) else 502
    raise HTTPException(status_code=status, detail=f"ABDM error [{code}]: {message}")
