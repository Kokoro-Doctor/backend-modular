"""
ABHA auth router — provision a loginable Kokoro user from an ABHA account.

Routed to AuthLambda via the existing API Gateway rule `/auth/{proxy+}`, so no
gateway change is needed.
"""
from fastapi import APIRouter, HTTPException

from app.logger import get_logger
from app.models import schemas
from app.services import abha_signup_service

logger = get_logger(__name__)

router = APIRouter(prefix="/auth/abha", tags=["abha-auth"])


@router.post("/signup-user")
def signup_user_from_abha(data: schemas.AbhaSignupRequest):
    """
    Create (or look up) a loginable Kokoro user from an existing ABHA account
    and link them. Identity (phone/name/email) is sourced from the ABHA record;
    only `abha_number` and `hospital_id` are required in the body.

    Returns a JWT so the user is immediately logged in. Idempotent — calling
    again with the same `abha_number` returns the same user.
    """
    try:
        return abha_signup_service.signup_user_from_abha(
            data.abha_number, data.hospital_id
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[AbhaSignup] Unexpected error")
        raise HTTPException(status_code=500, detail=str(exc))
