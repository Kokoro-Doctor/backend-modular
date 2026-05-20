"""
ABHA API endpoints.

Flow A — Create new ABHA (Aadhaar-based):
  POST /abha/create/request-otp   → request_creation_otp
  POST /abha/create/verify-otp    → create_abha

Flow B — Profile & card (requires Kokoro JWT + stored ABHA user token):
  GET  /abha/profile              → get_profile
  GET  /abha/card                 → get_card

Flow C — Login with existing ABHA:
  POST /abha/login/request-otp    → request_login_otp
  POST /abha/login/verify-otp     → verify_login
"""
import base64
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import config
from app.services import abha_service, user_abha_service
from app.abdm.schemas import ABHAProfile, ABDMTokens
from app.utils.jwt_utils import get_user_id_from_token
from app.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/abha", tags=["ABHA"])


# ---------------------------------------------------------------------------
# Request / Response schemas
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
    Step 1 of ABHA creation.
    Encrypts the Aadhaar number and requests an OTP to the registered mobile.
    Returns txnId to be passed in the next step.
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
def create_abha(body: CreateVerifyOTPRequest, authorization: Optional[str] = Header(None)):
    """
    Step 2 of ABHA creation.
    Verifies the OTP and creates the ABHA account.
    If the user is authenticated (Kokoro JWT provided), the ABHA data is
    persisted in the Users table automatically.
    """
    try:
        result = abha_service.create_abha_by_aadhaar(body.txn_id, body.otp, body.mobile)

        # Persist ABHA data if a Kokoro user token is present
        user_id: Optional[str] = None
        if authorization:
            try:
                user_id = get_user_id_from_token(authorization)
                user_abha_service.update_user_abha(
                    user_id=user_id,
                    profile=result.ABHAProfile,
                    tokens=result.tokens,
                    verification_type="AADHAAR_OTP",
                )
            except HTTPException as jwt_err:
                # A JWT error here is non-fatal — the ABHA was still created
                logger.warning("[ABHA] create_abha JWT error (non-fatal): %s", jwt_err.detail)

        response = {
            "message": result.message,
            "txn_id": result.txnId,
            "is_new": result.isNew,
            "abha_profile": result.ABHAProfile.model_dump(exclude_none=True),
            "tokens": result.tokens.model_dump(),
        }
        if user_id:
            response["abha_saved"] = True

        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ABHA] create_abha failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Flow B — Profile & Card
# ---------------------------------------------------------------------------

@router.get("/profile")
def get_profile(authorization: Optional[str] = Header(None)):
    """
    Fetch the ABHA profile for the authenticated Kokoro user.

    Token handling:
      1. Load user from DynamoDB
      2. get_valid_user_token() checks expiry and refreshes automatically if needed
      3. Call ABDM with the valid token
      4. Sync only profile fields back to DynamoDB (tokens are NOT overwritten)
    """
    try:
        user_id = get_user_id_from_token(authorization)
        user = _get_user_or_raise(user_id)

        # This handles expiry check + refresh internally
        valid_token = user_abha_service.get_valid_user_token(user_id, user)

        profile = abha_service.get_abha_profile(valid_token)

        # Sync profile fields only — does not touch token fields
        user_abha_service.sync_abha_profile(user_id, profile)

        return {"abha_profile": profile.model_dump(exclude_none=True)}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ABHA] get_profile failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/card")
def get_card(authorization: Optional[str] = Header(None)):
    """
    Download the ABHA card PDF for the authenticated Kokoro user.

    Token handling: same as get_profile — auto-refresh if expired.
    Saves the PDF to S3 and returns both a presigned URL and Base64 string.
    """
    try:
        user_id = get_user_id_from_token(authorization)
        user = _get_user_or_raise(user_id)

        # Auto-refresh if expired
        valid_token = user_abha_service.get_valid_user_token(user_id, user)

        pdf_bytes = abha_service.download_abha_card_bytes(valid_token)
        user_abha_service.save_abha_card_to_s3(user_id, pdf_bytes)

        download_url = user_abha_service.get_abha_card_presigned_url(user_id)
        card_base64 = base64.b64encode(pdf_bytes).decode("utf-8")

        return {
            "download_url": download_url,
            "card_base64": card_base64,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ABHA] get_card failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Flow C — ABHA Login
# ---------------------------------------------------------------------------

@router.post("/login/request-otp")
def request_login_otp(body: LoginOTPRequest):
    """
    Step 1 of ABHA login.
    Encrypts the ABHA number and requests an OTP.
    Returns txnId to be passed in verify-otp.
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
def verify_login(body: LoginVerifyOTPRequest, authorization: Optional[str] = Header(None)):
    """
    Step 2 of ABHA login.
    Verifies the OTP and returns ABHA user tokens + profile.
    If a Kokoro JWT is provided, ABHA data is persisted in the Users table.
    """
    try:
        data = abha_service.verify_abha_login(body.txn_id, body.otp)

        tokens_data = data.get("tokens", {})
        profile_data = data.get("ABHAProfile") or data.get("abhaProfile") or {}

        tokens = ABDMTokens(**tokens_data) if tokens_data else None
        profile = ABHAProfile(**profile_data) if profile_data else None

        user_id: Optional[str] = None
        if authorization and tokens and profile:
            try:
                user_id = get_user_id_from_token(authorization)
                user_abha_service.update_user_abha(
                    user_id=user_id,
                    profile=profile,
                    tokens=tokens,
                    verification_type="ABHA_NUMBER_OTP",
                )
            except HTTPException as jwt_err:
                logger.warning("[ABHA] verify_login JWT error (non-fatal): %s", jwt_err.detail)

        response = {
            "message": data.get("message", "Login verified"),
            "tokens": tokens.model_dump() if tokens else tokens_data,
            "abha_profile": profile.model_dump(exclude_none=True) if profile else profile_data,
        }
        if user_id:
            response["abha_saved"] = True

        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ABHA] verify_login failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_user_or_raise(user_id: str) -> dict:
    response = config.users_table.get_item(Key={"user_id": user_id})
    user = response.get("Item")
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
