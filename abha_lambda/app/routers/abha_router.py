"""
ABHA API endpoints — standalone, no Kokoro JWT required.

All authentication is done via the ABHA user token returned by ABDM after
create / login. The frontend stores it locally and sends it in X-ABHA-Token.

Flow A — Create new ABHA (Aadhaar-based):
  POST /abha/create/request-otp   → send OTP to Aadhaar-linked mobile
  POST /abha/create/verify-otp    → verify OTP → returns profile + ABHA token

Flow B — Login with existing ABHA:
  POST /abha/login/request-otp    → send OTP
  POST /abha/login/verify-otp     → verify OTP → returns profile + ABHA token

Flow C — Profile & card (requires ABHA token from Flow A or B):
  GET  /abha/profile              → live fetch from ABDM
  GET  /abha/card                 → download card PDF
"""
import base64
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.services import abha_service
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
# Flow A — ABHA Creation
# ---------------------------------------------------------------------------

@router.post("/create/request-otp")
def request_creation_otp(body: CreateOTPRequest):
    """
    Step 1 — Encrypt Aadhaar and request an OTP to the Aadhaar-linked mobile.
    Returns txn_id to carry into the next step.
    No authentication required.
    """
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
    Returns the full ABHA profile and user tokens.
    The frontend stores the token locally for subsequent profile/card calls.
    """
    try:
        result = abha_service.create_abha_by_aadhaar(body.txn_id, body.otp, body.mobile)
        return {
            "message": result.message,
            "txn_id": result.txnId,
            "is_new": result.isNew,
            "abha_profile": result.ABHAProfile.model_dump(exclude_none=True),
            "tokens": result.tokens.model_dump(),
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
    """
    Step 1 — Encrypt ABHA number and request a login OTP.
    No authentication required.
    """
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
    Returns the full ABHA profile and user tokens.
    The frontend stores the token locally for subsequent profile/card calls.
    """
    try:
        data = abha_service.verify_abha_login(body.txn_id, body.otp)

        tokens_data = data.get("tokens") or {}
        profile_data = data.get("ABHAProfile") or {}

        tokens = (
            ABDMTokens(**tokens_data)
            if all(tokens_data.get(k) for k in ("token", "expiresIn", "refreshToken", "refreshExpiresIn"))
            else None
        )
        profile = ABHAProfile(**profile_data) if profile_data else None

        return {
            "message": data.get("message", "Login verified"),
            "tokens": tokens.model_dump() if tokens else tokens_data,
            "abha_profile": profile.model_dump(exclude_none=True) if profile else profile_data,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ABHA] verify_login failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Flow C — Profile & Card (ABHA token required)
# ---------------------------------------------------------------------------

@router.get("/profile")
def get_profile(x_abha_token: Optional[str] = Header(None)):
    """
    Fetch the ABHA profile live from ABDM.
    Requires X-ABHA-Token header (the token returned by create/login).
    """
    valid_token = _extract_abha_token(x_abha_token)
    try:
        profile = abha_service.get_abha_profile(valid_token)
        return {"abha_profile": profile.model_dump(exclude_none=True)}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ABHA] get_profile failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/card")
def get_card(x_abha_token: Optional[str] = Header(None)):
    """
    Download the ABHA card as Base64 PDF.
    Requires X-ABHA-Token header.
    """
    valid_token = _extract_abha_token(x_abha_token)
    try:
        pdf_bytes = abha_service.download_abha_card_bytes(valid_token)
        card_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
        return {"card_base64": card_base64}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ABHA] get_card failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_abha_token(x_abha_token: Optional[str]) -> str:
    """Pull the raw token out of 'Bearer <token>' or raise 401."""
    if not x_abha_token:
        raise HTTPException(
            status_code=401,
            detail="X-ABHA-Token header missing. Complete ABHA create or login first.",
        )
    return x_abha_token.removeprefix("Bearer ").strip()
