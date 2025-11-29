from datetime import datetime, timezone
import random
from typing import Optional

from fastapi import APIRouter, HTTPException

from app import config
from app.logger import get_logger
from app.models import schemas
from app.utils.db_utils import (
    ensure_auth_record,
    get_auth_record,
    get_auth_token_by_phone,
    get_doctor_by_id,
    get_doctor_by_phone,
    get_user_by_id,
    get_user_by_phone,
    normalize_phone_number,
    update_auth_record,
)
from app.utils.jwt_utils import create_jwt
from app.utils.rate_limiter import RateLimitAction, rate_limit_guard
from app.utils.sms_utils import send_otp_sms
from app.utils.tokens import generate_token_id, ttl_minutes_from_now

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["passwordless-auth"])

LOGIN_OTP_PURPOSE = "login_otp"
SIGNUP_OTP_PURPOSE = "signup_otp"
OTP_TTL_MINUTES = 5


def _delete_token(token_id: str, purpose: str) -> None:
    try:
        config.auth_tokens_table.delete_item(
            Key={"token_id": token_id, "purpose": purpose}
        )
    except Exception:
        logger.exception("[AuthTokens] Failed to delete token %s (%s)", token_id, purpose)


def _record_has_account(record: dict) -> bool:
    if not record:
        return False
    return bool(record.get("user_id") or record.get("doctor_id"))


def _account_exists_error() -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "error": "ACCOUNT_EXISTS",
            "message": "You already have an account. Please login to continue."
        }
    )


def _not_registered_error() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "error": "NOT_REGISTERED",
            "message": "Please sign up first."
        }
    )


def _otp_send_failure() -> HTTPException:
    return HTTPException(status_code=500, detail="Failed to send OTP. Please try again later.")


def _login_discovery_response(role: str) -> dict:
    """Passwordless login discovery - always returns OTP required."""
    return {
        "role": role,
        "has_password": False,
        "message": "OTP required to continue."
    }


def _dispatch_otp(phone_number: str, purpose: str, role: Optional[str] = None) -> None:
    otp = f"{random.randint(1000, 9999)}"
    token_id = generate_token_id()
    token_item = {
        "token_id": token_id,
        "purpose": purpose,
        "phoneNumber": phone_number,
        "token": otp,
        "ttl": ttl_minutes_from_now(OTP_TTL_MINUTES),
        "role": role,
        "createdAt": datetime.now(timezone.utc).isoformat()
    }
    try:
        send_otp_sms(phone_number, otp)
        config.auth_tokens_table.put_item(Item=token_item)
    except Exception as exc:
        logger.exception("[OTP] Failed to send OTP to %s: %s", phone_number, exc)
        raise _otp_send_failure()


def validate_otp(normalized_phone: str, otp: str, purpose: str) -> dict:
    """Validate OTP and delete the token. Raises HTTPException on failure."""
    token_item = get_auth_token_by_phone(normalized_phone, purpose)
    if not token_item:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")

    ttl = token_item.get("ttl")
    now_ts = int(datetime.now(timezone.utc).timestamp())
    if ttl and ttl < now_ts:
        _delete_token(token_item.get("token_id"), token_item.get("purpose", purpose))
        raise HTTPException(status_code=400, detail="OTP expired. Please request a new one.")

    if token_item["token"] != otp:
        raise HTTPException(status_code=400, detail="Invalid OTP. Please check and try again.")

    _delete_token(token_item.get("token_id"), token_item.get("purpose", purpose))
    return token_item


def _build_user_payload(user: dict) -> dict:
    if not user:
        return {}
    return {
        "user_id": user.get("user_id"),
        "name": user.get("name") or user.get("username"),
        "email": user.get("email"),
        "phoneNumber": user.get("phoneNumber"),
    }


def _build_doctor_payload(doctor: dict) -> dict:
    if not doctor:
        return {}
    return {
        "doctor_id": doctor.get("doctor_id"),
        "name": doctor.get("name") or doctor.get("doctorname"),
        "email": doctor.get("email"),
        "phoneNumber": doctor.get("phoneNumber"),
        "specialization": doctor.get("specialization"),
        "experience": doctor.get("experience"),
    }


def _issue_login_response(role: str, phone_number: str, profile: dict) -> dict:
    token_kwargs = {
        "phone_number": phone_number,
        "role": role,
        "user_id": profile.get("user_id"),
        "doctor_id": profile.get("doctor_id"),
    }
    access_token = create_jwt(**token_kwargs)
    response = {
        "verified": True,
        "is_existing_user": True,
        "role": role,
        "access_token": access_token,
        "profile": profile,
        "message": "OTP verified. Logged in.",
    }
    response.update(
        {
            "user_id": profile.get("user_id"),
            "doctor_id": profile.get("doctor_id"),
        }
    )
    return response


def _load_profile_by_role(role: str, record: dict, normalized_phone: str) -> dict:
    if role == "user":
        user_id = record.get("user_id")
        user = get_user_by_id(user_id) if user_id else get_user_by_phone(normalized_phone)
        if not user:
            raise HTTPException(status_code=404, detail="User record missing. Please contact support.")
        return _build_user_payload(user)

    if role == "doctor":
        doctor_id = record.get("doctor_id")
        doctor = get_doctor_by_id(doctor_id) if doctor_id else get_doctor_by_phone(normalized_phone)
        if not doctor:
            raise HTTPException(status_code=404, detail="Doctor record missing. Please contact support.")
        return _build_doctor_payload(doctor)

    raise HTTPException(status_code=400, detail="Unknown account role. Please contact support.")


def _handle_signup_otp_request(phone_number: str, role: str):
    normalized_phone = normalize_phone_number(phone_number)
    if not normalized_phone:
        raise HTTPException(status_code=400, detail="Invalid phone number")

    record = get_auth_record(normalized_phone)
    if record and _record_has_account(record):
        raise _account_exists_error()

    ensure_auth_record(normalized_phone)

    with rate_limit_guard(RateLimitAction.MOBILE_OTP, normalized_phone):
        _dispatch_otp(normalized_phone, SIGNUP_OTP_PURPOSE, role)

    logger.info("[SignupOTP] Sent %s OTP to %s", role, normalized_phone)
    return {"message": "OTP sent successfully."}


@router.post("/user/request-signup-otp")
def request_user_signup_otp(data: schemas.SignupOtpRequest):
    return _handle_signup_otp_request(data.phoneNumber, "user")


@router.post("/doctor/request-signup-otp")
def request_doctor_signup_otp(data: schemas.SignupOtpRequest):
    return _handle_signup_otp_request(data.phoneNumber, "doctor")


@router.post("/request-otp")
def request_otp(data: schemas.LoginOtpRequest):
    """Request OTP for passwordless login."""
    normalized_phone = normalize_phone_number(data.phoneNumber)
    if not normalized_phone:
        raise HTTPException(status_code=400, detail="Invalid phone number")

    record = get_auth_record(normalized_phone)
    if not record or not _record_has_account(record):
        raise _not_registered_error()

    with rate_limit_guard(RateLimitAction.MOBILE_OTP, normalized_phone):
        _dispatch_otp(normalized_phone, LOGIN_OTP_PURPOSE, record.get("role"))

    logger.info("[LoginOTP] OTP sent to %s for %s", normalized_phone, record.get("role"))
    return {"message": "OTP sent successfully."}


@router.post("/verify-signup-otp")
@router.post("/verify-otp")  # Backwards compatibility
def verify_signup_otp(data: schemas.SignupOtpVerify):
    """
    Optional endpoint to verify OTP before signup.
    Note: OTP can also be verified directly in /user/signup or /doctor/signup endpoints.
    """
    normalized_phone = normalize_phone_number(data.phoneNumber)
    if not normalized_phone:
        raise HTTPException(status_code=400, detail="Invalid phone number")

    record = get_auth_record(normalized_phone)
    if record and _record_has_account(record):
        raise _account_exists_error()

    validate_otp(normalized_phone, data.otp, SIGNUP_OTP_PURPOSE)
    logger.info("[VerifySignupOTP] Phone %s verified for %s signup.", normalized_phone, data.role)
    return {
        "verified": True,
        "role": data.role,
        "message": "OTP verified. You can now complete signup."
    }


@router.post("/login")
def login(data: schemas.LoginRequest):
    """Passwordless login - OTP only."""
    normalized_phone = normalize_phone_number(data.phoneNumber)
    if not normalized_phone:
        raise HTTPException(status_code=400, detail="Invalid phone number")

    record = get_auth_record(normalized_phone)
    if not record or not _record_has_account(record):
        raise _not_registered_error()

    role = record.get("role")
    if not role:
        role = "doctor" if record.get("doctor_id") else "user" if record.get("user_id") else None
    if not role:
        raise HTTPException(status_code=400, detail="Account role missing. Please contact support.")

    # If no OTP provided, return discovery response
    if not data.otp:
        return _login_discovery_response(role)

    # Verify OTP and login
    otp = data.otp.strip()
    if not otp:
        raise HTTPException(status_code=400, detail="OTP cannot be empty.")
    
    return _login_with_otp(normalized_phone, record, otp)


def _login_with_otp(normalized_phone: str, record: dict, otp: str):
    validate_otp(normalized_phone, otp, LOGIN_OTP_PURPOSE)

    role = record.get("role")
    now_iso = datetime.now(timezone.utc).isoformat()
    updates = {
        "is_verified": True,
        "role": role,
        "last_login": now_iso,
        "last_verified_at": now_iso,
        "updated_at": now_iso
    }
    update_auth_record(normalized_phone, updates)

    profile = _load_profile_by_role(role, record, normalized_phone)
    logger.info("[Login] OTP login successful for %s (%s)", normalized_phone, role)
    return _issue_login_response(role, normalized_phone, profile)
