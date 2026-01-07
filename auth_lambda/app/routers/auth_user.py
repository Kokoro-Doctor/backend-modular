"""
User auth router - thin wrapper around user and auth services.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.logger import get_logger
from app.models import schemas
from app.services.auth_service import SIGNUP_OTP_PURPOSE, validate_otp
from app.services.user_service import (
    user_exists_by_phone,
    user_exists_by_email,
    create_user_profile,
)
from app.services.auth_service import (
    ensure_auth_record,
    update_auth_record,
)
from app.utils.db_utils import normalize_phone_number
from app.utils.jwt_utils import create_jwt

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["user-auth"])


@router.post("/user/signup")
def user_signup(data: schemas.UserProfileCreate):
    try:
        normalized_phone = normalize_phone_number(data.phoneNumber)
        if not normalized_phone:
            raise HTTPException(status_code=400, detail="Invalid phone number")
        
        # Validate email format (Pydantic already validates, but ensure it's provided)
        normalized_email = data.email.lower().strip()
        if not normalized_email or "@" not in normalized_email:
            raise HTTPException(status_code=400, detail="Valid email is required")

        # Validate OTP by email (since signup OTP is sent only to email)
        validate_otp(normalized_email, data.otp, SIGNUP_OTP_PURPOSE)

        # Check if user already exists by phone
        if user_exists_by_phone(normalized_phone):
            raise HTTPException(status_code=400, detail="Phone number already registered.")
        
        # Check if user already exists by email
        if user_exists_by_email(normalized_email):
            raise HTTPException(status_code=400, detail="Email already registered.")

        # Ensure auth record exists with email
        ensure_auth_record(normalized_phone, normalized_email)

        # Create user profile
        user_item = create_user_profile(
            {"name": data.name, "email": data.email},
            normalized_phone
        )

        # Update auth record with user_id and mark email as verified
        now_iso = datetime.now(timezone.utc).isoformat()
        update_auth_record(
            normalized_phone,
            {
                "role": "user",
                "user_id": user_item["user_id"],
                "doctor_id": None,
                "is_verified": True,
                "email_verified": True,  # Email verified during signup
                "phone_verified": False,  # Phone not verified during signup
                "last_login": now_iso,
                "updated_at": now_iso
            }
        )

        # Create JWT token (Auth service returns JWT only, not full profile)
        access_token = create_jwt(
            phone_number=normalized_phone,
            role="user",
            user_id=user_item["user_id"]
        )

        logger.info("[UserSignup] Created user %s", user_item["user_id"])
        return {
            "message": "User profile created successfully.",
            "access_token": access_token,
            "user_id": user_item["user_id"]
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[UserSignup] Unexpected error")
        raise HTTPException(status_code=500, detail=str(exc))


