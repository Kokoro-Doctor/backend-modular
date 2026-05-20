"""
ABDM gateway token manager.

Fetches a client-credentials token from the ABDM sessions endpoint and caches
it in module-level memory. Lambda execution context reuse means the cache
survives across warm invocations; a cold start triggers one extra fetch.
"""
import threading
import time
import uuid
from datetime import datetime, timezone

import requests

from app import config
from app.logger import get_logger

logger = get_logger(__name__)

_lock = threading.Lock()
_cache: dict = {"token": None, "expires_at": 0.0}

# Refresh the token this many seconds before it actually expires to avoid
# racing the expiry on a slow ABDM response.
_EXPIRY_BUFFER_SECONDS = 60


def get_access_token() -> str:
    """Return a valid ABDM gateway access token, fetching a new one if needed."""
    with _lock:
        if _cache["token"] and time.time() < _cache["expires_at"] - _EXPIRY_BUFFER_SECONDS:
            return _cache["token"]
        return _fetch_and_cache()


def _fetch_and_cache() -> str:
    url = f"{config.ABDM_GATEWAY_BASE_URL}/api/hiecm/gateway/v3/sessions"
    headers = {
        "Content-Type": "application/json",
        "REQUEST-ID": str(uuid.uuid4()),
        "TIMESTAMP": _utc_timestamp(),
        "X-CM-ID": config.ABDM_X_CM_ID,
    }
    payload = {
        "clientId": config.ABDM_CLIENT_ID,
        "clientSecret": config.ABDM_CLIENT_SECRET,
        "grantType": "client_credentials",
    }

    logger.info("[TokenManager] Fetching new ABDM gateway token")
    resp = requests.post(url, json=payload, headers=headers, timeout=15)

    if resp.status_code != 200:
        logger.error(
            "[TokenManager] Failed to fetch token: status=%s body=%s",
            resp.status_code,
            resp.text,
        )
        raise RuntimeError(f"ABDM session API error {resp.status_code}: {resp.text}")

    data = resp.json()
    token = data["accessToken"]
    expires_in = int(data.get("expiresIn", 1200))

    _cache["token"] = token
    _cache["expires_at"] = time.time() + expires_in
    logger.info("[TokenManager] Token cached, expires in %ss", expires_in)
    return token


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
           f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"
