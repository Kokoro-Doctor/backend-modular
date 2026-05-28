"""
ABDM ABHA business logic.

All methods call the ABDM APIs via abdm/client.py and return typed dicts
or Pydantic models. Sensitive values (Aadhaar, OTP) are encrypted before
being sent — they are NEVER logged.
"""
from typing import Optional

from app.abdm import client as abdm_client
from app.abdm import encryption
from app.abdm.schemas import (
    ABHAProfile,
    ABDMTokens,
    EnrollmentOTPResponse,
    EnrollmentResponse,
    LoginOTPResponse,
)
from app.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 1. Request OTP for ABHA creation (Aadhaar-based enrollment)
# ---------------------------------------------------------------------------

def request_abha_creation_otp(aadhaar: str) -> EnrollmentOTPResponse:
    """
    Encrypt the Aadhaar number and request an OTP for ABHA enrollment.
    Returns txnId and message.
    """
    logger.info("[ABHAService] Requesting ABHA creation OTP")
    encrypted_aadhaar = encryption.encrypt_value(aadhaar)

    payload = {
        "txnId": "",
        "scope": ["abha-enrol"],
        "loginHint": "aadhaar",
        "loginId": encrypted_aadhaar,
        "otpSystem": "aadhaar",
    }
    data = abdm_client.post("/abha/api/v3/enrollment/request/otp", payload)
    logger.info("[ABHAService] Creation OTP requested, txnId=%s", data.get("txnId"))
    return EnrollmentOTPResponse(**data)


# ---------------------------------------------------------------------------
# 2. Create ABHA by Aadhaar OTP verification
# ---------------------------------------------------------------------------

def create_abha_by_aadhaar(txn_id: str, otp: str, mobile: str) -> EnrollmentResponse:
    """
    Encrypt the OTP and complete ABHA enrollment by Aadhaar.
    Returns ABHAProfile, user tokens, and isNew flag.
    """
    logger.info("[ABHAService] Enrolling ABHA by Aadhaar, txnId=%s", txn_id)
    encrypted_otp = encryption.encrypt_value(otp)

    payload = {
        "authData": {
            "authMethods": ["otp"],
            "otp": {
                "txnId": txn_id,
                "otpValue": encrypted_otp,
                "mobile": mobile,
            },
        },
        "consent": {
            "code": "abha-enrollment",
            "version": "1.4",
        },
    }
    data = abdm_client.post("/abha/api/v3/enrollment/enrol/byAadhaar", payload)
    logger.info(
        "[ABHAService] ABHA enrolled, isNew=%s ABHANumber=%s",
        data.get("isNew"),
        data.get("ABHAProfile", {}).get("ABHANumber"),
    )
    return EnrollmentResponse(**data)


# ---------------------------------------------------------------------------
# 3. Get ABHA profile (requires user token)
# ---------------------------------------------------------------------------

def get_abha_profile(user_token: str) -> ABHAProfile:
    """Fetch the full ABHA profile for the authenticated user."""
    logger.info("[ABHAService] Fetching ABHA profile")
    data = abdm_client.get("/abha/api/v3/profile/account", user_token=user_token)
    return ABHAProfile(**data)


# ---------------------------------------------------------------------------
# 4. Download ABHA card PDF (requires user token)
# ---------------------------------------------------------------------------

def download_abha_card_bytes(user_token: str) -> bytes:
    """Download the ABHA card as raw PDF bytes."""
    logger.info("[ABHAService] Downloading ABHA card")
    resp = abdm_client.get(
        "/abha/api/v3/profile/account/abha-card",
        user_token=user_token,
        raw=True,
    )
    return resp.content


# ---------------------------------------------------------------------------
# 5. Request OTP for ABHA login (ABHA number-based)
# ---------------------------------------------------------------------------

def request_abha_login_otp(abha_number: str) -> LoginOTPResponse:
    """
    Encrypt the ABHA number and request a login OTP.
    Returns txnId and message.
    """
    logger.info("[ABHAService] Requesting ABHA login OTP")
    encrypted_abha = encryption.encrypt_value(abha_number)

    payload = {
        "scope": ["abha-login", "aadhaar-verify"],
        "loginHint": "abha-number",
        "loginId": encrypted_abha,
        "otpSystem": "aadhaar",
    }
    data = abdm_client.post("/abha/api/v3/profile/login/request/otp", payload)
    logger.info("[ABHAService] Login OTP requested, txnId=%s", data.get("txnId"))
    return LoginOTPResponse(**data)


# ---------------------------------------------------------------------------
# 6. Verify ABHA login OTP
# ---------------------------------------------------------------------------

def verify_abha_login(txn_id: str, otp: str) -> dict:
    """
    Encrypt the OTP and verify the ABHA login.
    Returns a normalized dict with a nested "tokens" key and "ABHAProfile".

    NOTE: ABDM's login/verify endpoint returns token fields at the TOP LEVEL
    (unlike enrollment/enrol/byAadhaar which nests them under "tokens").
    We normalize here so the router always sees the same shape:
      { "tokens": { "token": ..., "expiresIn": ..., ... }, "ABHAProfile": {...} }
    """
    logger.info("[ABHAService] Verifying ABHA login OTP, txnId=%s", txn_id)
    encrypted_otp = encryption.encrypt_value(otp)

    payload = {
        "scope": ["abha-login", "aadhaar-verify"],
        "authData": {
            "authMethods": ["otp"],
            "otp": {
                "txnId": txn_id,
                "otpValue": encrypted_otp,
            },
        },
    }
    data = abdm_client.post("/abha/api/v3/profile/login/verify", payload)

    # ABDM login verify: token fields are at top level, not under "tokens"
    # Handle both shapes defensively (in case ABDM ever normalises their API)
    nested = data.get("tokens") or {}
    tokens_normalized = {
        "token":            data.get("token")           or nested.get("token"),
        "expiresIn":        data.get("expiresIn")       or nested.get("expiresIn"),
        "refreshToken":     data.get("refreshToken")    or nested.get("refreshToken"),
        "refreshExpiresIn": data.get("refreshExpiresIn") or nested.get("refreshExpiresIn"),
    }

    abha_profile = data.get("ABHAProfile") or data.get("abhaProfile")
    logger.info(
        "[ABHAService] Login verified, ABHANumber=%s",
        abha_profile.get("ABHANumber") if abha_profile else "unknown",
    )

    return {
        "tokens": tokens_normalized,
        "ABHAProfile": abha_profile,
        "message": data.get("message"),
    }


# ---------------------------------------------------------------------------
# 7. Refresh ABHA user token (called automatically — not a public endpoint)
# ---------------------------------------------------------------------------

def refresh_user_token(refresh_token: str) -> ABDMTokens:
    """
    Exchange an ABDM refresh token for a new access + refresh token pair.

    This is called internally by user_abha_service.get_valid_user_token()
    when the stored access token has expired. It is NOT exposed as an API
    endpoint — the refresh happens transparently to the caller.

    ABDM endpoint: POST /abha/api/v3/profile/login/verify/user/token
    Body:          { "refreshToken": "<refresh_token>" }
    Response:      { "token": "...", "expiresIn": 1800,
                     "refreshToken": "...", "refreshExpiresIn": 1296000 }
    """
    logger.info("[ABHAService] Calling ABDM token refresh endpoint")
    payload = {"refreshToken": refresh_token}
    data = abdm_client.post("/abha/api/v3/profile/login/verify/user/token", payload)
    logger.info("[ABHAService] Token refresh successful")
    return ABDMTokens(**data)
