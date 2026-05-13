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
from app.utils.email_utils import send_otp_email
from app.utils.tokens import generate_token_id, ttl_minutes_from_now
# Auth service no longer imports user/doctor services
# Profile loading is handled by User/Doctor services separately

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


def get_auth_record_by_email(email: str):
    """Fetch auth metadata by email using GSI. Returns None if not found."""
    try:
        normalized_email = email.lower().strip()
        response = config.auth_table.query(
            IndexName="email-index",
            KeyConditionExpression=Key("email").eq(normalized_email)
        )
        items = response.get("Items", [])
        return items[0] if items else None
    except Exception as e:
        logger.error(f"[get_auth_record_by_email] Error querying auth record by email {email}: {e}")
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


def ensure_auth_record(phoneNumber: str, email: Optional[str] = None) -> dict:
    """Retrieve an auth record, creating a new shell if needed."""
    normalized = normalize_phone_number(phoneNumber)
    if not normalized:
        raise ValueError("Invalid phone number for auth record creation")

    existing = get_auth_record(normalized)
    if existing:
        # Update email if provided and not already set
        if email and not existing.get("email"):
            normalized_email = email.lower().strip()
            update_auth_record(normalized, {"email": normalized_email})
            existing["email"] = normalized_email
        return existing

    now = datetime.now(timezone.utc).isoformat()
    record = {
        "phoneNumber": normalized,
        "role": None,
        "is_verified": False,
        "email_verified": False,  # Track email verification separately
        "phone_verified": False,  # Track phone verification separately
        "user_id": None,
        "doctor_id": None,
        "created_at": now,
        "updated_at": now,
        "last_login": None,
        "last_verified_at": None
    }
    
    # Add email if provided
    if email:
        record["email"] = email.lower().strip()
    
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


def get_auth_token_by_email(email: str, purpose: str):
    """Get auth token by email and purpose using GSI. Returns the most recent non-expired token."""
    try:
        normalized_email = email.lower().strip()
        response = config.auth_tokens_table.query(
            IndexName="email-index",
            KeyConditionExpression=Key("email").eq(normalized_email) & Key("purpose").eq(purpose)
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
        logger.error(f"[get_auth_token_by_email] Error querying token by email {email}: {e}")
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


def _login_discovery_response(role: str, record: dict) -> dict:
    """
    Return login discovery response.
    For experimental flow users (no email), OTP is optional.
    For normal users (with email), OTP is required.
    """
    has_email = bool(record.get("email"))
    is_experimental_flow = not has_email
    
    if is_experimental_flow:
        return {
            "role": role,
            "message": "OTP optional. You can login directly or request OTP.",
            "otp_required": False
        }
    else:
        return {
            "role": role,
            "message": "OTP required to continue.",
            "otp_required": True
        }


def dispatch_otp(phone_number: Optional[str], email: Optional[str], purpose: str, 
                 preferred_channel: str = "sms", role: Optional[str] = None) -> None:
    """
    Dispatch OTP via SMS or email and store token.
    
    Args:
        phone_number: Normalized phone number (optional)
        email: Email address (optional)
        purpose: OTP purpose (login_otp or signup_otp)
        preferred_channel: "sms" or "email" - determines where to send OTP
        role: User role (user/doctor) for signup OTPs
    """
    otp = f"{random.randint(1000, 9999)}"
    token_id = generate_token_id()
    token_item = {
        "token_id": token_id,
        "purpose": purpose,
        "token": otp,
        "ttl": ttl_minutes_from_now(OTP_TTL_MINUTES),
        "role": role,
        "createdAt": datetime.now(timezone.utc).isoformat()
    }
    
    # Store both identifiers if available for flexible lookup
    if phone_number:
        token_item["phoneNumber"] = phone_number
    if email:
        normalized_email = email.lower().strip()
        token_item["email"] = normalized_email
    
    try:
        # Send OTP to preferred channel
        if preferred_channel == "email" and email:
            send_otp_email(email, otp)
            logger.info("[dispatch_otp] OTP sent via email to %s", email)
        elif phone_number:
            send_otp_sms(phone_number, otp)
            logger.info("[dispatch_otp] OTP sent via SMS to %s", phone_number)
        else:
            raise ValueError("No valid channel for OTP delivery")
        
        config.auth_tokens_table.put_item(Item=token_item)
    except Exception as exc:
        logger.exception("[OTP] Failed to send OTP: %s", exc)
        raise _otp_send_failure()


def validate_otp(identifier: str, otp: str, purpose: str) -> dict:
    """
    Validate OTP by email or phone and delete the token.
    Raises HTTPException on failure.
    
    Args:
        identifier: Email address or phone number
        otp: OTP code to validate
        purpose: OTP purpose (login_otp or signup_otp)
    """
    # Detect if identifier is email or phone
    is_email = "@" in identifier
    
    if is_email:
        normalized_identifier = identifier.lower().strip()
        token_item = get_auth_token_by_email(normalized_identifier, purpose)
    else:
        normalized_identifier = normalize_phone_number(identifier)
        if not normalized_identifier:
            raise HTTPException(status_code=400, detail="Invalid identifier")
        token_item = get_auth_token_by_phone(normalized_identifier, purpose)
    
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


def _issue_login_response(role: str, phone_number: str, record: dict) -> dict:
    """
    Issue login response with JWT token.
    Auth service returns JWT with IDs only, not full profiles.
    Clients should call User/Doctor services separately if profile is needed.
    """
    user_id = record.get("user_id")
    doctor_id = record.get("doctor_id")
    
    token_kwargs = {
        "phone_number": phone_number,
        "role": role,
        "user_id": user_id,
        "doctor_id": doctor_id,
    }
    access_token = create_jwt(**token_kwargs)
    
    response = {
        "verified": True,
        "is_existing_user": True,
        "role": role,
        "access_token": access_token,
        "message": "Logged in successfully.",
    }
    
    # Include IDs in response (not full profiles)
    if user_id:
        response["user_id"] = user_id
    if doctor_id:
        response["doctor_id"] = doctor_id
    
    return response


def handle_signup_otp_request(phone_number: str, email: str, role: str) -> dict:
    """
    Handle signup OTP request with both phone and email.
    Email is mandatory, OTP is sent ONLY to email (not SMS).
    """
    normalized_phone = normalize_phone_number(phone_number)
    if not normalized_phone:
        raise HTTPException(status_code=400, detail="Invalid phone number")
    
    normalized_email = email.lower().strip()
    if not normalized_email or "@" not in normalized_email:
        raise HTTPException(status_code=400, detail="Invalid email address")

    # Check if phone already has an account
    record = get_auth_record(normalized_phone)
    if record and _record_has_account(record):
        raise _account_exists_error()
    
    # Check if email already exists in auth table
    email_record = get_auth_record_by_email(normalized_email)
    if email_record and _record_has_account(email_record):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "EMAIL_EXISTS",
                "message": "Email already registered. Please login instead."
            }
        )

    # Ensure auth record exists with email
    ensure_auth_record(normalized_phone, normalized_email)

    # Use phone for rate limiting (primary identifier)
    # OTP is sent ONLY to email during signup
    with rate_limit_guard(RateLimitAction.MOBILE_OTP, normalized_phone):
        dispatch_otp(normalized_phone, normalized_email, SIGNUP_OTP_PURPOSE, "email", role)

    logger.info("[SignupOTP] Sent %s OTP to email %s (email-only during signup)", role, normalized_email)
    return {"message": "OTP sent successfully to your email address."}


def handle_login_otp_request(identifier: str, preferred_channel: str = "email") -> dict:
    """
    Handle login OTP request - identifier can be email or phone number.
    User can choose preferred channel (email or sms), default is email.
    """
    # Detect identifier type
    is_email = "@" in identifier
    
    if is_email:
        normalized_email = identifier.lower().strip()
        if not normalized_email or "@" not in normalized_email:
            raise HTTPException(status_code=400, detail="Invalid email address")
        
        # Lookup auth record by email
        record = get_auth_record_by_email(normalized_email)
        if not record or not _record_has_account(record):
            raise _not_registered_error()
        
        # Get phone number from record (for rate limiting and OTP storage)
        phone_number = record.get("phoneNumber")
        if not phone_number:
            raise HTTPException(status_code=400, detail="Phone number not found for account")
        
        # Send OTP to preferred channel (default email)
        # If SMS requested but no phone verified, fallback to email
        if preferred_channel == "sms":
            # Check if phone is verified, if not, use email
            if not record.get("phone_verified", False):
                logger.warning("[LoginOTP] Phone not verified, falling back to email for %s", normalized_email)
                preferred_channel = "email"
        
        with rate_limit_guard(RateLimitAction.MOBILE_OTP, phone_number):
            dispatch_otp(phone_number, normalized_email, LOGIN_OTP_PURPOSE, preferred_channel, record.get("role"))
        
        channel_name = "email" if preferred_channel == "email" else "SMS"
        logger.info("[LoginOTP] OTP sent to %s via %s for %s", normalized_email if preferred_channel == "email" else phone_number, channel_name, record.get("role"))
    else:
        normalized_phone = normalize_phone_number(identifier)
        if not normalized_phone:
            raise HTTPException(status_code=400, detail="Invalid phone number")
        
        record = get_auth_record(normalized_phone)
        if not record or not _record_has_account(record):
            raise _not_registered_error()
        
        # Get email from record if available
        email = record.get("email")
        
        # Send OTP to preferred channel (default email)
        # If SMS requested but no phone verified, fallback to email
        if preferred_channel == "sms":
            if not record.get("phone_verified", False) and email:
                logger.warning("[LoginOTP] Phone not verified, falling back to email for %s", normalized_phone)
                preferred_channel = "email"
            elif not email:
                raise HTTPException(status_code=400, detail="Email not found. Please use email login.")
        
        with rate_limit_guard(RateLimitAction.MOBILE_OTP, normalized_phone):
            dispatch_otp(normalized_phone, email, LOGIN_OTP_PURPOSE, preferred_channel, record.get("role"))
        
        channel_name = "email" if preferred_channel == "email" else "SMS"
        logger.info("[LoginOTP] OTP sent to %s via %s for %s", email if preferred_channel == "email" else normalized_phone, channel_name, record.get("role"))
    
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


def login_with_otp(phone_number: str, record: dict, otp: str, identifier: str) -> dict:
    """
    Handle login with OTP verification.
    Returns JWT with user_id/doctor_id only, not full profiles.
    
    Args:
        phone_number: Normalized phone number (for auth record update)
        record: Auth record
        otp: OTP code to validate
        identifier: Original identifier used (email or phone) for OTP validation
    """
    validate_otp(identifier, otp, LOGIN_OTP_PURPOSE)

    role = record.get("role")
    now_iso = datetime.now(timezone.utc).isoformat()
    updates = {
        "is_verified": True,
        "role": role,
        "last_login": now_iso,
        "last_verified_at": now_iso,
        "updated_at": now_iso
    }
    update_auth_record(phone_number, updates)

    logger.info("[Login] OTP login successful for %s (%s)", phone_number, role)
    # Return JWT with IDs only, not full profiles
    return _issue_login_response(role, phone_number, record)


def handle_login(identifier: str, otp: Optional[str] = None) -> dict:
    """
    Handle login request - identifier can be email or phone number.
    Returns discovery response if no OTP, otherwise logs in.
    For experimental flow users (no email), OTP is optional.
    """
    # Detect identifier type and get auth record
    is_email = "@" in identifier
    
    if is_email:
        normalized_email = identifier.lower().strip()
        if not normalized_email or "@" not in normalized_email:
            raise HTTPException(status_code=400, detail="Invalid email address")
        
        record = get_auth_record_by_email(normalized_email)
        if not record or not _record_has_account(record):
            raise _not_registered_error()
        
        phone_number = record.get("phoneNumber")
        if not phone_number:
            raise HTTPException(status_code=400, detail="Phone number not found for account")
    else:
        normalized_phone = normalize_phone_number(identifier)
        if not normalized_phone:
            raise HTTPException(status_code=400, detail="Invalid phone number")
        
        record = get_auth_record(normalized_phone)
        if not record or not _record_has_account(record):
            raise _not_registered_error()
        
        phone_number = normalized_phone

    role = record.get("role")
    if not role:
        role = "doctor" if record.get("doctor_id") else "user" if record.get("user_id") else None
    if not role:
        raise HTTPException(status_code=400, detail="Account role missing. Please contact support.")

    # Check if user is from experimental flow (no email)
    has_email = bool(record.get("email"))
    is_experimental_flow = not has_email
    
    # If no OTP provided
    if not otp:
        # # For experimental flow users, allow login without OTP
        # if is_experimental_flow:
        logger.info("[Login] Experimental flow login without OTP for phone %s", phone_number)
        now_iso = datetime.now(timezone.utc).isoformat()
        update_auth_record(
            phone_number,
            {
                "last_login": now_iso,
                "updated_at": now_iso
            }
        )
        return _issue_login_response(role, phone_number, record)
        # else:
        #     # For normal users, return discovery response indicating OTP is required
        #     return _login_discovery_response(role, record)

    # OTP provided - validate and login
    otp_clean = otp.strip()
    if not otp_clean:
        raise HTTPException(status_code=400, detail="OTP cannot be empty.")
    
    return login_with_otp(phone_number, record, otp_clean, identifier)


def initiate_session() -> dict:
    """Create a new anonymous session and return session_id. Sessions expire after 7 days."""
    from app.utils.tokens import generate_token_id, ttl_minutes_from_now
    
    session_id = generate_token_id()
    now_iso = datetime.now(timezone.utc).isoformat()
    
    # 7 days TTL (7 * 24 * 60 = 10080 minutes)
    ttl = ttl_minutes_from_now(7 * 24 * 60)
    
    # Optionally store in DynamoDB if SESSIONS_TABLE is configured
    if config.sessions_table:
        try:
            session_item = {
                "session_id": session_id,
                "created_at": now_iso,
                "ttl": ttl
            }
            config.sessions_table.put_item(Item=session_item)
            logger.info(f"[SessionInitiate] Created session {session_id} in DynamoDB")
        except Exception as e:
            logger.warning(f"[SessionInitiate] Failed to store session in DynamoDB: {e}. Continuing with session ID generation.")
    
    logger.info(f"[SessionInitiate] Created anonymous session: {session_id}")
    return {"session_id": session_id}

