from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app import config
from app.logger import get_logger
from app.models import schemas
from app.routers.auth_common import SIGNUP_OTP_PURPOSE, validate_otp
from app.utils.db_utils import (
    ensure_auth_record,
    generate_user_id,
    get_user_by_phone,
    normalize_phone_number,
    update_auth_record,
)
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

        user_id = generate_user_id()
        now_iso = datetime.now(timezone.utc).isoformat()

        user_item = {
            "user_id": user_id,
            "name": data.name.strip(),
            "phoneNumber": normalized_phone,
            "createdAt": now_iso,
        }

        if data.email:
            user_item["email"] = data.email.lower()

        config.users_table.put_item(Item=user_item)

        update_auth_record(
            normalized_phone,
            {
                "role": "user",
                "user_id": user_id,
                "doctor_id": None,
                "is_verified": True,
                "last_login": now_iso,
                "updated_at": now_iso
            }
        )

        profile = {
            "user_id": user_id,
            "name": user_item["name"],
            "phoneNumber": normalized_phone,
            "email": user_item.get("email"),
        }

        access_token = create_jwt(
            phone_number=normalized_phone,
            role="user",
            user_id=user_id
        )

        logger.info("[UserSignup] Created user %s", user_id)
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
