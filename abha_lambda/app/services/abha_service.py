"""
ABDM ABHA business logic.

All methods call the ABDM APIs via abdm/client.py and return typed dicts
or Pydantic models. Sensitive values (Aadhaar, OTP) are encrypted before
being sent — they are NEVER logged.
"""
from datetime import datetime, timezone
from typing import Optional

from app.abdm import client as abdm_client
from app.abdm import encryption
from app.abdm.schemas import (
    ABHAProfile,
    ABDMTokens,
    EnrollmentOTPResponse,
    EnrollmentResponse,
    LoginOTPResponse,
    MobileLoginVerifyResponse,
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
# 2b. ABHA Mobile Verification (3.0 Step 4) — link/verify a mobile that is NOT
#     the Aadhaar-linked one. Runs AFTER create_abha_by_aadhaar, chained on the
#     same enrollment txnId. Without this, the mobile is never linked to the
#     ABHA record, so mobile login (7.4) returns ABDM-1115.
#       Step 4a: send OTP to the mobile   →  request_mobile_verify_otp
#       Step 4b: verify the OTP           →  verify_mobile_verify_otp
# ---------------------------------------------------------------------------

def request_mobile_verify_otp(txn_id: str, mobile: str) -> EnrollmentOTPResponse:
    """
    Encrypt the mobile number and request an OTP to verify it against the ABHA
    being enrolled (3.0 Step 4a). Returns txnId and message.
    """
    logger.info("[ABHAService] Requesting mobile-verify OTP, txnId=%s", txn_id)
    encrypted_mobile = encryption.encrypt_value(mobile)

    payload = {
        "txnId": txn_id,
        "scope": ["abha-enrol", "mobile-verify"],
        "loginHint": "mobile",
        "loginId": encrypted_mobile,
        "otpSystem": "abdm",
    }
    data = abdm_client.post("/abha/api/v3/enrollment/request/otp", payload)
    logger.info("[ABHAService] Mobile-verify OTP requested, txnId=%s", data.get("txnId"))
    return EnrollmentOTPResponse(**data)


def verify_mobile_verify_otp(txn_id: str, otp: str) -> dict:
    """
    Encrypt the OTP and verify the mobile number (3.0 Step 4b).

    NOTE: this uses the /enrollment/auth/byAbdm endpoint (not enrol/byAadhaar),
    and ABDM returns only { txnId, authResult, message } — no tokens/profile.
    Returns that dict verbatim.
    """
    logger.info("[ABHAService] Verifying mobile-verify OTP, txnId=%s", txn_id)
    encrypted_otp = encryption.encrypt_value(otp)

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"

    payload = {
        "scope": ["abha-enrol", "mobile-verify"],
        "authData": {
            "authMethods": ["otp"],
            "otp": {
                "timeStamp": timestamp,
                "txnId": txn_id,
                "otpValue": encrypted_otp,
            },
        },
    }
    data = abdm_client.post("/abha/api/v3/enrollment/auth/byAbdm", payload)
    logger.info(
        "[ABHAService] Mobile-verify result=%s txnId=%s",
        data.get("authResult"), data.get("txnId"),
    )
    return data


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
        accept="image/png",
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

    # ABDM login/verify does not return ABHAProfile — fetch it separately using the token
    if not abha_profile and tokens_normalized.get("token"):
        try:
            fetched = get_abha_profile(tokens_normalized["token"])
            abha_profile = fetched.model_dump(exclude_none=True)
        except Exception:
            logger.warning("[ABHAService] Could not fetch profile after login verify", exc_info=True)

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
# 7. Login via Mobile OTP (7.4) — Step 1: request OTP to the mobile number
# ---------------------------------------------------------------------------

def request_mobile_login_otp(mobile: str) -> LoginOTPResponse:
    """
    Encrypt the mobile number and request a login OTP (7.4 Step 1).

    Unlike ABHA-number login (which uses the Aadhaar OTP system), this targets
    the mobile directly via the ABDM OTP system. Returns txnId and message.
    """
    logger.info("[ABHAService] Requesting mobile login OTP")
    encrypted_mobile = encryption.encrypt_value(mobile)

    payload = {
        "scope": ["abha-login", "mobile-verify"],
        "loginHint": "mobile",
        "loginId": encrypted_mobile,
        "otpSystem": "abdm",
    }
    data = abdm_client.post("/abha/api/v3/profile/login/request/otp", payload)
    logger.info("[ABHAService] Mobile login OTP requested, txnId=%s", data.get("txnId"))
    return LoginOTPResponse(**data)


# ---------------------------------------------------------------------------
# 8. Login via Mobile OTP (7.4) — Step 2: verify OTP -> T-token + accounts list
# ---------------------------------------------------------------------------

def verify_mobile_login_otp(txn_id: str, otp: str) -> MobileLoginVerifyResponse:
    """
    Encrypt the OTP and verify the mobile login OTP (7.4 Step 2).

    Does NOT create a session. Returns a SHORT-LIVED (5 min) T-token plus the
    list of ABHA accounts linked to the mobile (a mobile can map to several).
    The caller must pick one account and call verify_mobile_login_user().
    """
    logger.info("[ABHAService] Verifying mobile login OTP, txnId=%s", txn_id)
    encrypted_otp = encryption.encrypt_value(otp)

    payload = {
        "scope": ["abha-login", "mobile-verify"],
        "authData": {
            "authMethods": ["otp"],
            "otp": {
                "txnId": txn_id,
                "otpValue": encrypted_otp,
            },
        },
    }
    data = abdm_client.post("/abha/api/v3/profile/login/verify", payload)
    logger.info(
        "[ABHAService] Mobile login OTP verified, txnId=%s accounts=%d",
        data.get("txnId"),
        len(data.get("accounts") or []),
    )
    return MobileLoginVerifyResponse(**data)


# ---------------------------------------------------------------------------
# 9. Login via Mobile OTP (7.4) — Step 3: select account -> final session token
# ---------------------------------------------------------------------------

def verify_mobile_login_user(txn_id: str, abha_number: str, t_token: str) -> dict:
    """
    Select one ABHA account and obtain the final session token (7.4 Step 3).

    `t_token` is the short-lived token from verify_mobile_login_otp(); it is
    sent in the `T-token` header (valid 5 minutes). Returns the same normalized
    shape as verify_abha_login(): { "tokens": {...}, "ABHAProfile": {...} }.
    """
    logger.info(
        "[ABHAService] Verifying mobile login user, txnId=%s ABHANumber=%s",
        txn_id, abha_number,
    )

    payload = {
        "ABHANumber": abha_number,
        "txnId": txn_id,
    }
    data = abdm_client.post(
        "/abha/api/v3/profile/login/verify/user",
        payload,
        extra_headers={"T-token": f"Bearer {t_token}"},
    )

    # verify/user returns token fields at the top level (like login/verify)
    nested = data.get("tokens") or {}
    tokens_normalized = {
        "token":            data.get("token")            or nested.get("token"),
        "expiresIn":        data.get("expiresIn")        or nested.get("expiresIn"),
        "refreshToken":     data.get("refreshToken")     or nested.get("refreshToken"),
        "refreshExpiresIn": data.get("refreshExpiresIn") or nested.get("refreshExpiresIn"),
    }

    abha_profile = data.get("ABHAProfile") or data.get("abhaProfile")

    # verify/user does not return ABHAProfile — fetch it separately using the token
    if not abha_profile and tokens_normalized.get("token"):
        try:
            fetched = get_abha_profile(tokens_normalized["token"])
            abha_profile = fetched.model_dump(exclude_none=True)
        except Exception:
            logger.warning("[ABHAService] Could not fetch profile after mobile login verify/user", exc_info=True)

    logger.info(
        "[ABHAService] Mobile login complete, ABHANumber=%s",
        abha_profile.get("ABHANumber") if abha_profile else abha_number,
    )

    return {
        "tokens": tokens_normalized,
        "ABHAProfile": abha_profile,
        "message": data.get("message"),
    }


# ---------------------------------------------------------------------------
# 10. Refresh ABHA user token (called automatically — not a public endpoint)
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
