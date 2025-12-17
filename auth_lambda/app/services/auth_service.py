"""
Auth service - handles authentication business logic (OTP, login, signup workflows).
"""
from datetime import datetime, timezone
import random
from typing import Optional

from fastapi import HTTPException

from app import config
from app.logger import get_logger
from app.utils.db_utils import (
    normalize_phone_number,
)
from boto3.dynamodb.conditions import Key
from datetime import datetime, timezone
from app.utils.jwt_utils import create_jwt
from app.utils.rate_limiter import RateLimitAction, rate_limit_guard
from app.utils.sms_utils import send_otp_sms
from app.utils.tokens import generate_token_id, ttl_minutes_from_now
from app.services.user_service import get_user_by_id, get_user_by_phone, build_user_payload
from app.services.doctor_service import get_doctor_by_id, get_doctor_by_phone, build_doctor_payload

logger = get_logger(__name__)

LOGIN_OTP_PURPOSE = "login_otp"
SIGNUP_OTP_PURPOSE = "signup_otp"
OTP_TTL_MINUTES = 5


def get_auth_record(phoneNumber: str):
    """Fetch auth metadata for a normalized phone number."""
    normalized = normalize_phone_number(phoneNumber)
    if not normalized:
        return None
    try:
        response = config.auth_table.get_item(Key={"phoneNumber": normalized})
        return response.get("Item")
    except Exception as e:
        logger.error(f"[get_auth_record] Error fetching auth record for {phoneNumber}: {e}")
        return None


def put_auth_record(item: dict) -> None:
    """Persist a complete auth record."""
    try:
        config.auth_table.put_item(Item=item)
    except Exception as e:
        logger.error(f"[put_auth_record] Failed to write auth record: {e}")
        raise


def update_auth_record(phoneNumber: str, updates: dict) -> dict:
    """Update specific fields on an auth record."""
    normalized = normalize_phone_number(phoneNumber)
    if not normalized:
        raise ValueError("Invalid phone number for auth record update")

    expressions = []
    attr_values = {}
    attr_names = {}
    for idx, (field, value) in enumerate(updates.items()):
        name_placeholder = f"#f{idx}"
        value_placeholder = f":v{idx}"
        expressions.append(f"{name_placeholder} = {value_placeholder}")
        attr_names[name_placeholder] = field
        attr_values[value_placeholder] = value

    update_expression = "SET " + ", ".join(expressions)

    try:
        response = config.auth_table.update_item(
            Key={"phoneNumber": normalized},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=attr_names,
            ExpressionAttributeValues=attr_values,
            ReturnValues="ALL_NEW"
        )
        return response.get("Attributes", {})
    except Exception as e:
        logger.error(f"[update_auth_record] Failed to update auth record for {phoneNumber}: {e}")
        raise


def ensure_auth_record(phoneNumber: str) -> dict:
    """Retrieve an auth record, creating a new shell if needed."""
    normalized = normalize_phone_number(phoneNumber)
    if not normalized:
        raise ValueError("Invalid phone number for auth record creation")

    existing = get_auth_record(normalized)
    if existing:
        return existing

    now = datetime.now(timezone.utc).isoformat()
    record = {
        "phoneNumber": normalized,
        "role": None,
        "is_verified": False,
        "user_id": None,
        "doctor_id": None,
        "created_at": now,
        "updated_at": now,
        "last_login": None,
        "last_verified_at": None
    }
    put_auth_record(record)
    return record


def get_auth_token_by_phone(phoneNumber: str, purpose: str):
    """Get auth token by phone and purpose using GSI. Returns the most recent non-expired token."""
    try:
        normalized_phone = normalize_phone_number(phoneNumber)
        response = config.auth_tokens_table.query(
            IndexName="phone-index",
            KeyConditionExpression=Key("phoneNumber").eq(normalized_phone) & Key("purpose").eq(purpose)
        )
        items = response.get("Items", [])
        if not items:
            return None
        
        # Sort by createdAt descending to get the most recent OTP first
        items.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        
        # Return the most recent non-expired OTP
        current_timestamp = int(datetime.now(timezone.utc).timestamp())
        for item in items:
            ttl = item.get("ttl")
            if not ttl or ttl >= current_timestamp:
                return item
        
        # If all are expired, return the most recent one anyway (for proper error handling)
        return items[0]
    except Exception as e:
        logger.error(f"[get_auth_token_by_phone] Error querying token by phone {phoneNumber}: {e}")
        return None


def _delete_token(token_id: str, purpose: str) -> None:
    """Delete an auth token"""
    try:
        config.auth_tokens_table.delete_item(
            Key={"token_id": token_id, "purpose": purpose}
        )
    except Exception:
        logger.exception("[AuthTokens] Failed to delete token %s (%s)", token_id, purpose)


def _record_has_account(record: dict) -> bool:
    """Check if auth record has an associated account"""
    if not record:
        return False
    return bool(record.get("user_id") or record.get("doctor_id"))


def _account_exists_error() -> HTTPException:
    """Return standard account exists error"""
    return HTTPException(
        status_code=400,
        detail={
            "error": "ACCOUNT_EXISTS",
            "message": "You already have an account. Please login to continue."
        }
    )


def _not_registered_error() -> HTTPException:
    """Return standard not registered error"""
    return HTTPException(
        status_code=404,
        detail={
            "error": "NOT_REGISTERED",
            "message": "Please sign up first."
        }
    )


def _otp_send_failure() -> HTTPException:
    """Return standard OTP send failure error"""
    return HTTPException(status_code=500, detail="Failed to send OTP. Please try again later.")


def _login_discovery_response(role: str) -> dict:
    """Return login discovery response"""
    return {
        "role": role,
        "message": "OTP required to continue."
    }


def dispatch_otp(phone_number: str, purpose: str, role: Optional[str] = None) -> None:
    """Dispatch OTP via SMS and store token"""
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


def _load_profile_by_role(role: str, record: dict, normalized_phone: str) -> dict:
    """Load user or doctor profile based on role"""
    if role == "user":
        user_id = record.get("user_id")
        user = get_user_by_id(user_id) if user_id else get_user_by_phone(normalized_phone)
        if not user:
            raise HTTPException(status_code=404, detail="User record missing. Please contact support.")
        return build_user_payload(user)

    if role == "doctor":
        doctor_id = record.get("doctor_id")
        doctor = get_doctor_by_id(doctor_id) if doctor_id else get_doctor_by_phone(normalized_phone)
        if not doctor:
            raise HTTPException(status_code=404, detail="Doctor record missing. Please contact support.")
        return build_doctor_payload(doctor)

    raise HTTPException(status_code=400, detail="Unknown account role. Please contact support.")


def _issue_login_response(role: str, phone_number: str, profile: dict) -> dict:
    """Issue login response with JWT token"""
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


def handle_signup_otp_request(phone_number: str, role: str) -> dict:
    """Handle signup OTP request"""
    normalized_phone = normalize_phone_number(phone_number)
    if not normalized_phone:
        raise HTTPException(status_code=400, detail="Invalid phone number")

    record = get_auth_record(normalized_phone)
    if record and _record_has_account(record):
        raise _account_exists_error()

    ensure_auth_record(normalized_phone)

    with rate_limit_guard(RateLimitAction.MOBILE_OTP, normalized_phone):
        dispatch_otp(normalized_phone, SIGNUP_OTP_PURPOSE, role)

    logger.info("[SignupOTP] Sent %s OTP to %s", role, normalized_phone)
    return {"message": "OTP sent successfully."}


def handle_login_otp_request(phone_number: str) -> dict:
    """Handle login OTP request"""
    normalized_phone = normalize_phone_number(phone_number)
    if not normalized_phone:
        raise HTTPException(status_code=400, detail="Invalid phone number")

    record = get_auth_record(normalized_phone)
    if not record or not _record_has_account(record):
        raise _not_registered_error()

    with rate_limit_guard(RateLimitAction.MOBILE_OTP, normalized_phone):
        dispatch_otp(normalized_phone, LOGIN_OTP_PURPOSE, record.get("role"))

    logger.info("[LoginOTP] OTP sent to %s for %s", normalized_phone, record.get("role"))
    return {"message": "OTP sent successfully."}


def verify_signup_otp(phone_number: str, otp: str, role: str) -> dict:
    """Verify signup OTP"""
    normalized_phone = normalize_phone_number(phone_number)
    if not normalized_phone:
        raise HTTPException(status_code=400, detail="Invalid phone number")

    record = get_auth_record(normalized_phone)
    if record and _record_has_account(record):
        raise _account_exists_error()

    validate_otp(normalized_phone, otp, SIGNUP_OTP_PURPOSE)
    logger.info("[VerifySignupOTP] Phone %s verified for %s signup.", normalized_phone, role)
    return {
        "verified": True,
        "role": role,
        "message": "OTP verified. You can now complete signup."
    }


def login_with_otp(normalized_phone: str, record: dict, otp: str) -> dict:
    """Handle login with OTP verification"""
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


def handle_login(phone_number: str, otp: Optional[str] = None) -> dict:
    """Handle login request - returns discovery response if no OTP, otherwise logs in"""
    normalized_phone = normalize_phone_number(phone_number)
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

    if not otp:
        return _login_discovery_response(role)

    otp_clean = otp.strip()
    if not otp_clean:
        raise HTTPException(status_code=400, detail="OTP cannot be empty.")
    
    return login_with_otp(normalized_phone, record, otp_clean)

