"""
ABDM public certificate manager.

Fetches and caches the RSA public key used to encrypt sensitive values
(Aadhaar, OTP, mobile) before sending them to the ABDM APIs.
"""
import threading
import uuid
from datetime import datetime, timezone

import base64

import requests
from cryptography.hazmat.primitives.serialization import load_der_public_key

from app import config
from app.abdm import token_manager
from app.logger import get_logger

logger = get_logger(__name__)

_lock = threading.Lock()
_cached_public_key = None  # cryptography RSAPublicKey object


def get_public_key():
    """Return the cached ABDM RSA public key, fetching if not yet loaded."""
    global _cached_public_key
    with _lock:
        if _cached_public_key is None:
            _cached_public_key = _fetch_public_key()
        return _cached_public_key


def invalidate():
    """Force a re-fetch on the next call (call this if encryption fails unexpectedly)."""
    global _cached_public_key
    with _lock:
        _cached_public_key = None


def _fetch_public_key():
    url = f"{config.ABDM_ABHA_BASE_URL}/abha/api/v3/profile/public/certificate"
    headers = {
        "REQUEST-ID": str(uuid.uuid4()),
        "TIMESTAMP": _utc_timestamp(),
        "Authorization": f"Bearer {token_manager.get_access_token()}",
    }

    logger.info("[CertManager] Fetching ABDM public certificate")
    resp = requests.get(url, headers=headers, timeout=15)

    if resp.status_code != 200:
        logger.error(
            "[CertManager] Failed to fetch certificate: status=%s body=%s",
            resp.status_code,
            resp.text,
        )
        raise RuntimeError(f"ABDM certificate API error {resp.status_code}: {resp.text}")

    # Response is a raw Base64 string representing DER-encoded binary — NOT PEM.
    # PEM would have -----BEGIN PUBLIC KEY----- headers; this doesn't.
    key_b64 = resp.text.strip()
    try:
        key_b64 = resp.json().get("publicKey", key_b64)
    except Exception:
        pass  # response was plain text, not JSON — use as-is

    key_der = base64.b64decode(key_b64)
    public_key = load_der_public_key(key_der)
    logger.info("[CertManager] Public certificate loaded and cached (DER)")
    return public_key


def _utc_timestamp() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
