"""
ABHA API endpoints — no auth required on any endpoint.

Token management is entirely backend-driven:
  • create / login  →  profile + tokens saved to AbhaAccounts DynamoDB table
  • profile / card  →  caller sends abha_number as query param
                       backend looks up stored ABDM token → auto-refreshes if needed
                       → calls ABDM live and returns result

Flow A — Create new ABHA (Aadhaar-based):
  POST /abha/create/request-otp   → request OTP
  POST /abha/create/verify-otp    → verify OTP → save to DB

Flow A2 — Verify a non-Aadhaar mobile (3.0 Step 4; needed for mobile login):
  POST /abha/create/mobile/request-otp  → OTP to the mobile (same enrol txn_id)
  POST /abha/create/mobile/verify-otp   → verify OTP → mobile linked to ABHA

Flow B — Login with existing ABHA number (Aadhaar OTP):
  POST /abha/login/request-otp    → request OTP
  POST /abha/login/verify-otp     → verify OTP → save to DB

Flow B2 — Login with mobile number (3-step; a mobile may map to many ABHAs):
  POST /abha/login/mobile/request-otp  → request OTP to the mobile
  POST /abha/login/mobile/verify-otp   → verify OTP → T-token + accounts list
  POST /abha/login/mobile/verify-user  → pick one ABHA → save to DB

Flow C — Profile & card (abha_number query param required):
  GET  /abha/profile?abha_number=XX-XXXX-XXXX-XXXX  → lookup by abha_number → live ABDM fetch
  GET  /abha/card?abha_number=XX-XXXX-XXXX-XXXX     → lookup by abha_number → ABDM download
"""
import base64

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import abha_service
from app.services import abha_accounts_service
from app.services import kokoro_user_service
from app.abdm.schemas import ABHAProfile, ABDMTokens
from app.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/abha", tags=["ABHA"])


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class CreateOTPRequest(BaseModel):
    aadhaar: str


class CreateVerifyOTPRequest(BaseModel):
    txn_id: str
    otp: str
    mobile: str


class MobileVerifyOTPRequest(BaseModel):
    txn_id: str
    mobile: str


class MobileVerifyConfirmRequest(BaseModel):
    txn_id: str
    otp: str


class LoginOTPRequest(BaseModel):
    abha_number: str


class LoginVerifyOTPRequest(BaseModel):
    txn_id: str
    otp: str


class MobileLoginOTPRequest(BaseModel):
    mobile: str


class MobileLoginVerifyOTPRequest(BaseModel):
    txn_id: str
    otp: str


class MobileLoginVerifyUserRequest(BaseModel):
    txn_id: str
    abha_number: str
    t_token: str


class SignupFromAbhaRequest(BaseModel):
    abha_number: str


# ---------------------------------------------------------------------------
# Flow A — ABHA Creation
# ---------------------------------------------------------------------------

@router.post("/create/request-otp")
def request_creation_otp(body: CreateOTPRequest):
    """Step 1 — Encrypt Aadhaar and request OTP to Aadhaar-linked mobile."""
    try:
        result = abha_service.request_abha_creation_otp(body.aadhaar)
        return {"txn_id": result.txnId, "message": result.message}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ABHA] request_creation_otp failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/create/verify-otp")
def create_abha(body: CreateVerifyOTPRequest):
    """
    Step 2 — Verify OTP and create / retrieve the ABHA account.
    Saves the full profile + tokens to AbhaAccounts table.
    """
    try:
        result = abha_service.create_abha_by_aadhaar(body.txn_id, body.otp, body.mobile)

        try:
            abha_accounts_service.save(result.ABHAProfile, result.tokens)
        except Exception:
            logger.exception("[ABHA] create_abha: DB save failed (non-fatal)")

        return {
            "message":      result.message,
            "txn_id":       result.txnId,
            "is_new":       result.isNew,
            "abha_number":  result.ABHAProfile.ABHANumber,
            "abha_profile": result.ABHAProfile.model_dump(exclude_none=True),
            "tokens":       result.tokens.model_dump(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ABHA] create_abha failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Flow A2 — ABHA Mobile Verification (3.0 Step 4)
#   Run AFTER /create/verify-otp when the chosen mobile differs from the
#   Aadhaar-linked one (ABHAProfile.mobile comes back null). Links the mobile
#   to the ABHA so it can later be used for mobile login. Uses the SAME txn_id
#   returned by /create/verify-otp.
# ---------------------------------------------------------------------------

@router.post("/create/mobile/request-otp")
def request_create_mobile_otp(body: MobileVerifyOTPRequest):
    """Step 4a — Encrypt the mobile and request an OTP to verify it."""
    try:
        result = abha_service.request_mobile_verify_otp(body.txn_id, body.mobile)
        return {"txn_id": result.txnId, "message": result.message}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ABHA] request_create_mobile_otp failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/create/mobile/verify-otp")
def verify_create_mobile_otp(body: MobileVerifyConfirmRequest):
    """
    Step 4b — Verify the mobile OTP. On success the mobile is linked to the
    ABHA. Returns ABDM's { txn_id, auth_result, message }. To see the updated
    mobile on the profile, call GET /abha/profile afterwards.
    """
    try:
        data = abha_service.verify_mobile_verify_otp(body.txn_id, body.otp)
        return {
            "message":     data.get("message", "Mobile verified"),
            "txn_id":      data.get("txnId"),
            "auth_result": data.get("authResult"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ABHA] verify_create_mobile_otp failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Flow B — ABHA Login
# ---------------------------------------------------------------------------

@router.post("/login/request-otp")
def request_login_otp(body: LoginOTPRequest):
    """Step 1 — Encrypt ABHA number and request login OTP."""
    try:
        result = abha_service.request_abha_login_otp(body.abha_number)
        return {"txn_id": result.txnId, "message": result.message}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ABHA] request_login_otp failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/login/verify-otp")
def verify_login(body: LoginVerifyOTPRequest):
    """
    Step 2 — Verify OTP and log in to ABHA.
    Saves the full profile + tokens to AbhaAccounts table.
    """
    try:
        data = abha_service.verify_abha_login(body.txn_id, body.otp)

        tokens_data  = data.get("tokens") or {}
        profile_data = data.get("ABHAProfile") or {}

        tokens = (
            ABDMTokens(**tokens_data)
            if all(tokens_data.get(k) for k in ("token", "expiresIn", "refreshToken", "refreshExpiresIn"))
            else None
        )
        profile = ABHAProfile(**profile_data) if profile_data else None

        if profile and tokens:
            try:
                abha_accounts_service.save(profile, tokens)
            except Exception:
                logger.exception("[ABHA] verify_login: DB save failed (non-fatal)")

        return {
            "message":      data.get("message", "Login verified"),
            "abha_number":  profile.ABHANumber if profile else None,
            "abha_profile": profile.model_dump(exclude_none=True) if profile else profile_data,
            "tokens":       tokens.model_dump() if tokens else tokens_data,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ABHA] verify_login failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Flow B2 — ABHA Login via Mobile Number (7.4) — 3-step flow
#   A mobile can map to multiple ABHA accounts, so step 2 returns the account
#   list + a short-lived T-token, and step 3 selects one account to get the
#   final session token.
# ---------------------------------------------------------------------------

@router.post("/login/mobile/request-otp")
def request_mobile_login_otp(body: MobileLoginOTPRequest):
    """Step 1 — Encrypt mobile number and request a login OTP."""
    try:
        result = abha_service.request_mobile_login_otp(body.mobile)
        return {"txn_id": result.txnId, "message": result.message}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ABHA] request_mobile_login_otp failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/login/mobile/verify-otp")
def verify_mobile_login_otp(body: MobileLoginVerifyOTPRequest):
    """
    Step 2 — Verify OTP. Returns a short-lived T-token plus the list of ABHA
    accounts linked to the mobile. NO session is created yet — the caller must
    pick one account and call /login/mobile/verify-user.
    """
    try:
        result = abha_service.verify_mobile_login_otp(body.txn_id, body.otp)
        return {
            "message":    result.message or "OTP verified",
            "txn_id":     result.txnId,
            "t_token":    result.token,
            "expires_in": result.expiresIn,
            "accounts":   [a.model_dump(exclude_none=True) for a in result.accounts],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ABHA] verify_mobile_login_otp failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/login/mobile/verify-user")
def verify_mobile_login_user(body: MobileLoginVerifyUserRequest):
    """
    Step 3 — Select one ABHA account (from step 2's list) and obtain the final
    session token. Saves the full profile + tokens to AbhaAccounts table.
    """
    try:
        data = abha_service.verify_mobile_login_user(body.txn_id, body.abha_number, body.t_token)

        tokens_data  = data.get("tokens") or {}
        profile_data = data.get("ABHAProfile") or {}

        tokens = (
            ABDMTokens(**tokens_data)
            if all(tokens_data.get(k) for k in ("token", "expiresIn", "refreshToken", "refreshExpiresIn"))
            else None
        )
        profile = ABHAProfile(**profile_data) if profile_data else None

        if profile and tokens:
            try:
                abha_accounts_service.save(profile, tokens)
            except Exception:
                logger.exception("[ABHA] verify_mobile_login_user: DB save failed (non-fatal)")

        return {
            "message":      data.get("message", "Login verified"),
            "abha_number":  profile.ABHANumber if profile else body.abha_number,
            "abha_profile": profile.model_dump(exclude_none=True) if profile else profile_data,
            "tokens":       tokens.model_dump() if tokens else tokens_data,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ABHA] verify_mobile_login_user failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Flow D — Provision a Kokoro website user from an ABHA account
#   Triggered AFTER ABHA creation (the AbhaAccounts row already exists). Creates
#   a Kokoro Users-table profile (link-or-create — reuses an existing user with
#   the same phone/email) and writes kokoro_user_id back onto the ABHA row.
#
#   NOTE: creates the Users profile ONLY — no AuthTable record — so the user
#   cannot OTP-login until auth_lambda provisions their auth record. See
#   kokoro_user_service for details.
# ---------------------------------------------------------------------------

@router.post("/signup-user")
def signup_user_from_abha(body: SignupFromAbhaRequest):
    """
    Provision (or look up) a Kokoro user for an existing ABHA account and link
    them. Returns { user_id, abha_number, created, already_linked }.
    """
    try:
        result = kokoro_user_service.signup_user_from_abha(body.abha_number)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ABHA] signup_user_from_abha failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Flow C — Profile & Card  (abha_number query param required, no auth)
# ---------------------------------------------------------------------------

@router.get("/profile")
def get_profile(abha_number: str):
    """
    Fetch the ABHA profile live from ABDM.
    Requires abha_number as a query parameter.
    Backend looks up stored ABDM token by abha_number and auto-refreshes if needed.
    """
    try:
        valid_token = abha_accounts_service.get_valid_token(abha_number)
        profile     = abha_service.get_abha_profile(valid_token)

        try:
            abha_accounts_service.update_profile(abha_number, profile)
        except Exception:
            logger.warning("[ABHA] get_profile: profile sync failed (non-fatal)")

        return {"abha_profile": profile.model_dump(exclude_none=True)}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ABHA] get_profile failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/card")
def get_card(abha_number: str):
    """
    Download the ABHA card as Base64 PNG.
    Requires abha_number as a query parameter.
    """
    try:
        valid_token = abha_accounts_service.get_valid_token(abha_number)
        pdf_bytes   = abha_service.download_abha_card_bytes(valid_token)
        return {"card_base64": base64.b64encode(pdf_bytes).decode("utf-8")}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ABHA] get_card failed")
        raise HTTPException(status_code=500, detail=str(e))
