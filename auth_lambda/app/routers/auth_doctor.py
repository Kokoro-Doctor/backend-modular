"""
Doctor auth router - thin wrapper around doctor and auth services.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.logger import get_logger
from app.models import schemas
from app.services.auth_service import SIGNUP_OTP_PURPOSE, validate_otp
from app.services.doctor_service import (
    doctor_exists_by_phone,
    create_doctor_profile,
)
from app.services.auth_service import (
    ensure_auth_record,
    update_auth_record,
    get_auth_record_by_email,
    _record_has_account,
)
from app.utils.db_utils import normalize_phone_number
from app.utils.jwt_utils import create_jwt

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["doctor-auth"])


@router.post("/doctor/signup")
def doctor_signup(data: schemas.DoctorProfileCreate):
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

        # Check if doctor already exists by phone
        if doctor_exists_by_phone(normalized_phone):
            raise HTTPException(status_code=400, detail="Phone number already registered.")
        
        # Check if email already exists in auth table (shared by users and doctors)
        email_record = get_auth_record_by_email(normalized_email)
        if email_record and _record_has_account(email_record):
            raise HTTPException(status_code=400, detail="Email already registered.")

        # Ensure auth record exists with email
        ensure_auth_record(normalized_phone, normalized_email)

        # Create doctor profile
        doctor_item = create_doctor_profile(
            {
                "name": data.name,
                "specialization": data.specialization,
                "experience": data.experience,
                "email": data.email
            },
            normalized_phone
        )

        # Update auth record with doctor_id and mark email as verified
        now_iso = datetime.now(timezone.utc).isoformat()
        update_auth_record(
            normalized_phone,
            {
                "role": "doctor",
                "doctor_id": doctor_item["doctor_id"],
                "user_id": None,
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
            role="doctor",
            doctor_id=doctor_item["doctor_id"]
        )

        logger.info("[DoctorSignup] Created doctor %s", doctor_item["doctor_id"])
        return {
            "message": "Doctor profile created successfully.",
            "access_token": access_token,
            "doctor_id": doctor_item["doctor_id"]
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[DoctorSignup] Unexpected error")
        raise HTTPException(status_code=500, detail=str(exc))
