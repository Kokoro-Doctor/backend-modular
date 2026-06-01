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

Flow B — Login with existing ABHA:
  POST /abha/login/request-otp    → request OTP
  POST /abha/login/verify-otp     → verify OTP → save to DB

Flow C — Profile & card (abha_number query param required):
  GET  /abha/profile?abha_number=XX-XXXX-XXXX-XXXX  → lookup by abha_number → live ABDM fetch
  GET  /abha/card?abha_number=XX-XXXX-XXXX-XXXX     → lookup by abha_number → ABDM download
"""
import base64

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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
    Download the ABHA card as Base64 PDF.
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
