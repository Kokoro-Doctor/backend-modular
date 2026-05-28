"""
Common auth router - thin wrapper around auth service.
"""
from fastapi import APIRouter

from app.models import schemas
from app.services.auth_service import (
    handle_login_otp_request,
    handle_login,
    initiate_session,
)

router = APIRouter(prefix="/auth", tags=["auth-common"])


@router.post("/request-otp")
def request_otp(data: schemas.LoginOtpRequest):
    # Login OTP can be sent to email (default) or SMS (if phone verified)
    return handle_login_otp_request(data.identifier, data.preferredChannel)


@router.post("/login")
def login(data: schemas.LoginRequest):
    return handle_login(data.identifier, data.otp)


@router.post("/session/initiate")
def session_initiate():
    """Create a new anonymous session for unauthenticated users."""
    return initiate_session()
