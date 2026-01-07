"""
Common auth router - thin wrapper around auth service.
"""
from fastapi import APIRouter

from app.models import schemas
from app.services.auth_service import (
    handle_login_otp_request,
    handle_signup_otp_request,
    handle_login,
    initiate_session,
)

router = APIRouter(prefix="/auth", tags=["otp-auth"])


@router.post("/user/request-signup-otp")
def request_user_signup_otp(data: schemas.SignupOtpRequest):
    # Signup OTP is sent ONLY to email
    return handle_signup_otp_request(data.phoneNumber, data.email, "user")


@router.post("/doctor/request-signup-otp")
def request_doctor_signup_otp(data: schemas.SignupOtpRequest):
    # Signup OTP is sent ONLY to email
    return handle_signup_otp_request(data.phoneNumber, data.email, "doctor")


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
