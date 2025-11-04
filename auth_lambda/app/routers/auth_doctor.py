from fastapi import APIRouter, HTTPException
from app.models import schemas
from app import config
from app.utils.security import hash_password, verify_password
from app.utils.tokens import generate_token, ttl_minutes_from_now
from app.utils.email_utils import send_brevo_email
from datetime import datetime, timezone
from app.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["doctor-auth"])

@router.post("/doctor/signup")
def doctor_signup(data: schemas.DoctorSignup):
    logger.info(f"[DoctorSignup] Received signup data: {data}")
    try:
        existing = config.doctors_table.get_item(Key={"email": data.email})
        logger.info(f"[DoctorSignup] DynamoDB get_item result: {existing}")

        if existing.get("Item"):
            raise HTTPException(status_code=400, detail="Email already registered")

        hashed = hash_password(data.password)
        logger.info(f"[DoctorSignup] Hashed password generated")

        config.doctors_table.put_item(Item={
            "email": data.email,
            "doctorname": data.doctorname,
            "phoneNumber": data.phoneNumber,
            "location": data.location,
            "password": hashed,
            "emailVerified": False,
            "phoneVerified": False,
            "onboarded": False,
            "createdAt": datetime.now(timezone.utc).isoformat()
        })

        token = generate_token()
        config.auth_tokens_table.put_item(Item={
            "email": data.email,
            "purpose": "email_verification",
            "token": token,
            "ttl": ttl_minutes_from_now(15)
        })

        verification_link = f"https://kokoro.doctor/verify-email?token={token}&email={data.email}"
        subject = "Verify your email for Kokoro Doctor"
        body = f"Click the link to verify your email: {verification_link}"
        send_brevo_email(data.email, subject, body)

        logger.info(f"[DoctorSignup] Verification email sent to {data.email}")
        logger.info(f"[DoctorSignup] Doctor {data.email} registered successfully")
        return {"message": "Doctor registered. Please complete profile setup."}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[DoctorSignup] Unexpected error")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/doctor/login")
def doctor_login(data: schemas.DoctorLogin):
    logger.info(f"[DoctorLogin] Received login data for {data.email}")
    try:
        doctor = config.doctors_table.get_item(Key={"email": data.email}).get("Item")
        if not doctor:
            logger.warning(f"[DoctorLogin] Doctor not found: {data.email}")
            raise HTTPException(status_code=400, detail="Doctor not found")

        if not verify_password(data.password, doctor["password"]):
            logger.warning(f"[DoctorLogin] Incorrect password for {data.email}")
            raise HTTPException(status_code=400, detail="Incorrect password")

        logger.info(f"[DoctorLogin] Login successful for {data.email}")
        return {
            "doctor": {
                "name": doctor.get("name") or doctor.get("doctorname"),
                "email": doctor["email"]
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[DoctorLogin] Unexpected error")
        raise HTTPException(status_code=500, detail=str(e))
