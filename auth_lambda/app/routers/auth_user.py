"""
User auth router - thin wrapper around user and auth services.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.logger import get_logger
from app.models import schemas
from app.services.auth_service import SIGNUP_OTP_PURPOSE, validate_otp
from app.services.user_service import (
    create_user_profile,
    get_user_by_phone,
    get_user_by_id,
    build_user_payload,
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

        # Validate OTP first
        validate_otp(normalized_phone, data.otp, SIGNUP_OTP_PURPOSE)

        existing_user = get_user_by_phone(normalized_phone)
        if existing_user:
            raise HTTPException(status_code=400, detail="Phone number already registered.")

        ensure_auth_record(normalized_phone)

        user_item = create_user_profile(
            {"name": data.name, "email": data.email},
            normalized_phone
        )

        now_iso = datetime.now(timezone.utc).isoformat()
        update_auth_record(
            normalized_phone,
            {
                "role": "user",
                "user_id": user_item["user_id"],
                "doctor_id": None,
                "is_verified": True,
                "last_login": now_iso,
                "updated_at": now_iso
            }
        )

        profile = build_user_payload(user_item)

        access_token = create_jwt(
            phone_number=normalized_phone,
            role="user",
            user_id=user_item["user_id"]
        )

        logger.info("[UserSignup] Created user %s", user_item["user_id"])
        return {
            "message": "User profile created successfully.",
            "access_token": access_token,
            "profile": profile
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[UserSignup] Unexpected error")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/user/{user_id}")
def get_user(user_id: str):
    """Get a single user by user_id"""
    try:
        user = get_user_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        profile = build_user_payload(user)
        logger.info("[GetUser] Retrieved user %s", user_id)
        return {"user": profile}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[GetUser] Unexpected error")
        raise HTTPException(status_code=500, detail=str(exc))
