"""
Common auth router - thin wrapper around auth service.
"""
from fastapi import APIRouter

from app.models import schemas
from app.services.auth_service import (
    handle_login_otp_request,
    handle_signup_otp_request,
    handle_login,
)

router = APIRouter(prefix="/auth", tags=["otp-auth"])


@router.post("/user/request-signup-otp")
def request_user_signup_otp(data: schemas.SignupOtpRequest):
    return handle_signup_otp_request(data.phoneNumber, "user")


@router.post("/doctor/request-signup-otp")
def request_doctor_signup_otp(data: schemas.SignupOtpRequest):
    return handle_signup_otp_request(data.phoneNumber, "doctor")


@router.post("/request-otp")
def request_otp(data: schemas.LoginOtpRequest):
    return handle_login_otp_request(data.phoneNumber)


@router.post("/login")
def login(data: schemas.LoginRequest):
    return handle_login(data.phoneNumber, data.otp)
