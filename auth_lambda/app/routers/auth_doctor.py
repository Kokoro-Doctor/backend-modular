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
    get_doctor_by_phone,
    create_default_slots_for_doctor,
)
from app.services.auth_service import (
    handle_signup_otp_request,
    ensure_auth_record,
    update_auth_record,
    get_auth_record_by_email,
    _record_has_account,
)
from app.utils.db_utils import normalize_phone_number
from app.utils.jwt_utils import create_jwt

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["doctor-auth"])

@router.post("/doctor/request-signup-otp")
def request_doctor_signup_otp(data: schemas.SignupOtpRequest):
    # Signup OTP is sent ONLY to email
    return handle_signup_otp_request(data.phoneNumber, data.email, "doctor")

@router.post("/doctor/signup")
def doctor_signup(data: schemas.DoctorProfileCreate):
    try:
        normalized_phone = normalize_phone_number(data.phoneNumber)
        if not normalized_phone:
            raise HTTPException(status_code=400, detail="Invalid phone number")
        
        # # Validate email format (Pydantic already validates, but ensure it's provided)
        # normalized_email = data.email.lower().strip()
        # if not normalized_email or "@" not in normalized_email:
        #     raise HTTPException(status_code=400, detail="Valid email is required")

        # Detect experimental flow: if email is not provided, use mobile-only flow
        is_experimental_flow = data.email is None
        
        if is_experimental_flow:
            # Experimental flow: mobile-only, no OTP, no email, name is optional
            logger.info("[DoctorSignup] Experimental flow detected for phone %s", normalized_phone)
            
            # Check if doctor already exists by phone
            existing_doctor = get_doctor_by_phone(normalized_phone)
            
            if existing_doctor:
                # Doctor already exists - prevent duplicate signup
                logger.info("[DoctorSignup] Doctor already exists: %s", existing_doctor.get("doctor_id"))
                raise HTTPException(status_code=400, detail="Phone number already registered.")
            
            # Doctor doesn't exist - create new doctor immediately
            logger.info("[DoctorSignup] Creating new doctor for phone %s", normalized_phone)
            
            # Prepare doctor data with name if provided
            doctor_data = {}
            if data.name:
                doctor_data["name"] = data.name
            if data.specialization:
                doctor_data["specialization"] = data.specialization
            if data.experience is not None:
                doctor_data["experience"] = data.experience
            
            # Create doctor profile with name if provided
            doctor_item = create_doctor_profile(doctor_data, normalized_phone)
            
            # Create default slots for the next 7 days
            try:
                create_default_slots_for_doctor(doctor_item["doctor_id"])
            except Exception as e:
                logger.warning(f"[DoctorSignup] Failed to create default slots for doctor {doctor_item['doctor_id']}: {e}")
                # Continue with signup even if slot creation fails
            
            # Ensure auth record exists (without email)
            ensure_auth_record(normalized_phone, None)
            
            # Update auth record with doctor_id
            now_iso = datetime.now(timezone.utc).isoformat()
            update_auth_record(
                normalized_phone,
                {
                    "role": "doctor",
                    "doctor_id": doctor_item["doctor_id"],
                    "user_id": None,
                    "is_verified": True,
                    "phone_verified": True,  # Phone is the primary identifier in experimental flow
                    "email_verified": False,
                    "last_login": now_iso,
                    "updated_at": now_iso
                }
            )
            
            # Create JWT token
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
        else:
            # Normal flow: email, OTP, name required
            normalized_email = data.email.lower().strip()
            if not normalized_email or "@" not in normalized_email:
                raise HTTPException(status_code=400, detail="Valid email is required")

        # # Validate OTP by email (since signup OTP is sent only to email)
        # validate_otp(normalized_email, data.otp, SIGNUP_OTP_PURPOSE)
        
            # Validate OTP by email (since signup OTP is sent only to email)
            if not data.otp:
                raise HTTPException(status_code=400, detail="OTP is required")
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

            # Create default slots for the next 7 days
            try:
                create_default_slots_for_doctor(doctor_item["doctor_id"])
            except Exception as e:
                logger.warning(f"[DoctorSignup] Failed to create default slots for doctor {doctor_item['doctor_id']}: {e}")
                # Continue with signup even if slot creation fails

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
