from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from app.models import schemas
from app import config
from app.utils.security import hash_password, verify_password
from app.utils.tokens import generate_token, ttl_minutes_from_now
from app.utils.email_utils import send_brevo_email
from datetime import datetime, timezone, timedelta
from app.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["user-auth"])

@router.post("/user/signup")
def user_signup(data: schemas.UserSignup):
    try:
        existing = config.users_table.get_item(Key={"email": data.email})

        if existing.get("Item"):
            name = existing.get("Item").get("username", "Another user")
            raise HTTPException(status_code=400, detail=f"A user named '{name}' is already registered with this email.")

        hashed = hash_password(data.password)

        config.users_table.put_item(Item={
            "username": data.username,
            "email": data.email,
            "password": hashed,
            "phoneNumber": data.phoneNumber,
            "location": data.location,
            "emailVerified": False,
            "phoneVerified": False,
            "createdAt": datetime.now(timezone.utc).isoformat()
        })
        logger.info(f"[UserSignup] User {data.email} registered successfully")
        # Create token
        token = generate_token()
        config.auth_tokens_table.put_item(Item={
            "email": data.email,
            "purpose": "email_verification",
            "token": token,
            "ttl": ttl_minutes_from_now(15)
        })
        # Send verification email
        verification_link = f"https://kokoro.doctor/verify-email?token={token}&email={data.email}"
        subject = "Verify your email for Kokoro Doctor"
        body = f"Click the link to verify your email: {verification_link}"
        send_brevo_email(data.email, subject, body)

        logger.info(f"[UserSignup] Verification email sent to {data.email}")
        return JSONResponse(
            content={"message": "User registered successfully"},
            headers={
                "Access-Control-Allow-Origin": "https://kokoro.doctor",
                "Access-Control-Allow-Credentials": "true"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[UserSignup] Unexpected error")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/user/login")
def user_login(data: schemas.UserLogin):
    logger.info(f"[UserLogin] Received login data for {data.email}")
    try:
        user = config.users_table.get_item(Key={"email": data.email}).get("Item")
        if not user:
            logger.warning(f"[UserLogin] User not found: {data.email}")
            raise HTTPException(status_code=400, detail="User not found")

        if not verify_password(data.password, user["password"]):
            logger.warning(f"[UserLogin] Incorrect password for {data.email}")
            raise HTTPException(status_code=400, detail="Incorrect password")
        # Temporary dummy token (replace with JWT ideally)
        access_token = generate_token()

        logger.info(f"[UserLogin] Login successful for {data.email}")
        return JSONResponse(
            content={
                "user": {
                    "name": user["username"],
                    "email": user["email"]
                },
                "access_token": access_token
            },
            headers={
                "Access-Control-Allow-Origin": "https://kokoro.doctor",
                "Access-Control-Allow-Credentials": "true"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[UserLogin] Unexpected error")
        raise HTTPException(status_code=500, detail=str(e))
