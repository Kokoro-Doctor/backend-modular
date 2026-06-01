"""
ABHA API endpoints.

Token management is entirely backend-driven:
  • create / login  →  profile + tokens saved to AbhaAccounts DynamoDB table
  • profile / card  →  frontend sends Kokoro JWT (Authorization header)
                       backend decodes JWT → extracts user_id → queries GSI
                       → fetches token from DB → auto-refreshes if needed
                       → calls ABDM live and returns result

Flow A — Create new ABHA (Aadhaar-based):
  POST /abha/create/request-otp   → request OTP
  POST /abha/create/verify-otp    → verify OTP → save to DB (with kokoro_user_id if logged in)

Flow B — Login with existing ABHA:
  POST /abha/login/request-otp    → request OTP
  POST /abha/login/verify-otp     → verify OTP → save to DB (with kokoro_user_id if logged in)

Flow C — Profile & card (Kokoro JWT required):
  GET  /abha/profile              → Authorization: Bearer <kokoro_jwt> → GSI lookup → live ABDM fetch
  GET  /abha/card                 → Authorization: Bearer <kokoro_jwt> → GSI lookup → ABDM download
"""
import base64
from typing import Optional

import jwt as pyjwt
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app import config
from app.services import abha_service
from app.services import abha_accounts_service
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

class LoginOTPRequest(BaseModel):
    abha_number: str

class LoginVerifyOTPRequest(BaseModel):
    txn_id: str
    otp: str


# ---------------------------------------------------------------------------
# JWT helper
# ---------------------------------------------------------------------------

def _decode_kokoro_jwt(authorization: Optional[str]) -> Optional[str]:
    """
    Decode the Kokoro JWT from the Authorization header and return user_id.
    Returns None if header is absent (unauthenticated call is allowed for
    create/login; required for profile/card which raise manually).
    Raises 401 if header is present but token is invalid/expired.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = pyjwt.decode(
            token,
            config.JWT_SECRET,
            algorithms=[config.JWT_ALGORITHM],
            issuer=config.JWT_ISSUER,
        )
        user_id = payload.get("user_id")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid Kokoro token: user_id missing.")
        return user_id
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Kokoro session expired. Please login again.")
    except pyjwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid Kokoro token: {e}")


def _require_kokoro_jwt(authorization: Optional[str]) -> str:
    """Like _decode_kokoro_jwt but raises 401 if header is missing."""
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Authorization header required. Please login to Kokoro first.",
        )
    user_id = _decode_kokoro_jwt(authorization)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authorization header required.")
    return user_id


# ---------------------------------------------------------------------------
# Flow A — ABHA Creation
# ---------------------------------------------------------------------------

@router.post("/create/request-otp")
def request_creation_otp(body: CreateOTPRequest):  # no auth — open
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
def create_abha(body: CreateVerifyOTPRequest, authorization: Optional[str] = Header(None)):
    """
    Step 2 — Verify OTP and create / retrieve the ABHA account.
    Saves the full profile + tokens to AbhaAccounts table.
    If the caller sends a Kokoro JWT, the ABHA record is linked to their user_id.
    """
    kokoro_user_id = _decode_kokoro_jwt(authorization)  # None if not logged in — OK
    try:
        result = abha_service.create_abha_by_aadhaar(body.txn_id, body.otp, body.mobile)

        try:
            abha_accounts_service.save(result.ABHAProfile, result.tokens, kokoro_user_id)
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
def verify_login(body: LoginVerifyOTPRequest, authorization: Optional[str] = Header(None)):
    """
    Step 2 — Verify OTP and log in to ABHA.
    Saves the full profile + tokens to AbhaAccounts table.
    If the caller sends a Kokoro JWT, the ABHA record is linked to their user_id.
    """
    kokoro_user_id = _decode_kokoro_jwt(authorization)  # None if not logged in — OK
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
                abha_accounts_service.save(profile, tokens, kokoro_user_id)
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
# Flow C — Profile & Card  (Kokoro JWT required)
# ---------------------------------------------------------------------------

@router.get("/profile")
def get_profile(authorization: Optional[str] = Header(None)):
    """
    Fetch the ABHA profile live from ABDM.
    Requires Authorization: Bearer <kokoro_jwt>.
    Backend decodes JWT → extracts user_id → queries GSI → gets stored ABDM token.
    """
    kokoro_user_id = _require_kokoro_jwt(authorization)
    try:
        valid_token = abha_accounts_service.get_valid_token_by_kokoro_user(kokoro_user_id)
        profile     = abha_service.get_abha_profile(valid_token)

        record = abha_accounts_service.get_by_kokoro_user_id(kokoro_user_id)
        abha_number = record.get("abha_number") if record else None
        if abha_number:
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
def get_card(authorization: Optional[str] = Header(None)):
    """
    Download the ABHA card as Base64 PDF.
    Requires Authorization: Bearer <kokoro_jwt>.
    """
    kokoro_user_id = _require_kokoro_jwt(authorization)
    try:
        valid_token = abha_accounts_service.get_valid_token_by_kokoro_user(kokoro_user_id)
        pdf_bytes   = abha_service.download_abha_card_bytes(valid_token)
        return {"card_base64": base64.b64encode(pdf_bytes).decode("utf-8")}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ABHA] get_card failed")
        raise HTTPException(status_code=500, detail=str(e))
