from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app import config
from app.logger import get_logger
from app.models import schemas
from app.routers.auth_common import SIGNUP_OTP_PURPOSE, validate_otp
from app.utils.db_utils import (
    ensure_auth_record,
    generate_doctor_id,
    get_doctor_by_phone,
    normalize_phone_number,
    update_auth_record,
)
from app.utils.jwt_utils import create_jwt

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["doctor-auth"])


@router.post("/doctor/signup")
def doctor_signup(data: schemas.DoctorProfileCreate):
    try:
        normalized_phone = normalize_phone_number(data.phoneNumber)
        if not normalized_phone:
            raise HTTPException(status_code=400, detail="Invalid phone number")

        # Validate OTP first
        validate_otp(normalized_phone, data.otp, SIGNUP_OTP_PURPOSE)

        existing_doctor = get_doctor_by_phone(normalized_phone)
        if existing_doctor:
            raise HTTPException(status_code=400, detail="Phone number already registered.")

        ensure_auth_record(normalized_phone)

        doctor_id = generate_doctor_id()
        now_iso = datetime.now(timezone.utc).isoformat()

        doctor_item = {
            "doctor_id": doctor_id,
            "doctorname": data.name.strip(),
            "phoneNumber": normalized_phone,
            "createdAt": now_iso,
        }

        if data.specialization:
            doctor_item["specialization"] = data.specialization
        if data.experience is not None:
            doctor_item["experience"] = data.experience
        if data.email:
            doctor_item["email"] = data.email.lower()

        config.doctors_table.put_item(Item=doctor_item)

        update_auth_record(
            normalized_phone,
            {
                "role": "doctor",
                "doctor_id": doctor_id,
                "user_id": None,
                "is_verified": True,
                "last_login": now_iso,
                "updated_at": now_iso
            }
        )

        profile = {
            "doctor_id": doctor_id,
            "name": doctor_item["doctorname"],
            "phoneNumber": normalized_phone,
            "email": doctor_item.get("email"),
            "specialization": doctor_item.get("specialization"),
            "experience": doctor_item.get("experience")
        }

        access_token = create_jwt(
            phone_number=normalized_phone,
            role="doctor",
            doctor_id=doctor_id
        )

        logger.info("[DoctorSignup] Created doctor %s", doctor_id)
        return {
            "message": "Doctor profile created successfully.",
            "access_token": access_token,
            "profile": profile
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[DoctorSignup] Unexpected error")
        raise HTTPException(status_code=500, detail=str(exc))
