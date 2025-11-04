from fastapi import APIRouter, HTTPException
from app import config
from app.utils.tokens import generate_token, ttl_minutes_from_now, ttl_days_from_now
from app.utils.email_utils import send_brevo_email
from app.utils.sms_utils import send_otp_sms
from app.utils.security import hash_password
from app.models import schemas
from datetime import datetime, timezone
from app.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["common-auth"])

@router.post("/session/initiate")
def initiate_session():
    session_id = generate_token()
    ttl = ttl_days_from_now(7)

    try:
        config.sessions_table.put_item(Item={
            "session_id": session_id,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "ttl": ttl
        })
        return {"session_id": session_id, "expires_in_days": 7}
    except Exception:
        logger.exception("[initiate_session] Failed to create session")
        raise HTTPException(status_code=500, detail="Failed to create session")

@router.post("/verify")
def verify_email(email: str, token: str):
    try:
        res = config.auth_tokens_table.get_item(Key={"email": email, "purpose": "email_verification"})
        item = res.get("Item")

        if not item or item["token"] != token:
            raise HTTPException(status_code=400, detail="Invalid or expired token")

        user = config.users_table.get_item(Key={"email": email}).get("Item")
        if user:
            config.users_table.update_item(
                Key={"email": email},
                UpdateExpression="SET emailVerified = :v",
                ExpressionAttributeValues={":v": True}
            )

        doctor = config.doctors_table.get_item(Key={"email": email}).get("Item")
        if doctor:
            config.doctors_table.update_item(
                Key={"email": email},
                UpdateExpression="SET emailVerified = :v",
                ExpressionAttributeValues={":v": True}
            )

        if not user and not doctor:
            raise HTTPException(status_code=404, detail="Email not found in any table")

        logger.info(f"[VerifyEmail] Email {email} verified successfully")
        config.auth_tokens_table.delete_item(Key={"email": email, "purpose": "email_verification"})
        return {"message": "Email verified successfully"}
    except HTTPException:
        raise
    except Exception:
        logger.exception("[VerifyEmail] Error")
        raise HTTPException(status_code=500, detail="Internal error during verification")


@router.post("/request-password-reset")
def request_password_reset(data: schemas.PasswordResetRequest):
    user = config.users_table.get_item(Key={"email": data.email}).get("Item")
    doctor = config.doctors_table.get_item(Key={"email": data.email}).get("Item")

    if not user and not doctor:
        raise HTTPException(status_code=404, detail="Email not registered")

    token = generate_token()
    expires = ttl_minutes_from_now(15)

    config.auth_tokens_table.put_item(Item={
        "email": data.email,
        "purpose": "password_reset",
        "token": token,
        "ttl": expires
    })

    reset_link = f"https://kokoro.doctor/reset-password?email={data.email}&token={token}"
    subject = "Reset Your Password"
    body = f"Click to reset your password: {reset_link}"
    send_brevo_email(data.email, subject, body)

    logger.info(f"[RequestPasswordReset] Password reset link sent to {data.email}")
    return {"message": "Password reset link sent to your email"}


@router.post("/reset-password")
def reset_password(data: schemas.PasswordResetConfirm):
    res = config.auth_tokens_table.get_item(Key={"email": data.email, "purpose": "password_reset"})
    item = res.get("Item")

    if not item or item["token"] != data.token:
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    hashed = hash_password(data.new_password)

    if config.users_table.get_item(Key={"email": data.email}).get("Item"):
        config.users_table.update_item(
            Key={"email": data.email},
            UpdateExpression="SET password = :p",
            ExpressionAttributeValues={":p": hashed}
        )
    elif config.doctors_table.get_item(Key={"email": data.email}).get("Item"):
        config.doctors_table.update_item(
            Key={"email": data.email},
            UpdateExpression="SET password = :p",
            ExpressionAttributeValues={":p": hashed}
        )
    else:
        raise HTTPException(status_code=404, detail="Email not found")

    config.auth_tokens_table.delete_item(Key={"email": data.email, "purpose": "password_reset"})
    return {"message": "Password reset successfully"}


@router.post("/send-mobile-otp")
def send_mobile_otp(data: schemas.MobileOTPRequest):
    otp = f"{__import__('random').randint(100000, 999999)}"
    logger.info(f"[SendMobileOTP] OTP for {data.phoneNumber} (User: {data.email}): {otp}")

    config.auth_tokens_table.put_item(Item={
        "email": data.email,
        "purpose": "mobile_otp",
        "token": otp,
        "phoneNumber": data.phoneNumber,
        "ttl": ttl_minutes_from_now(5)
    })

    send_otp_sms(data.phoneNumber, otp)
    return {"message": "OTP sent to your mobile number"}


@router.post("/verify-mobile-otp")
def verify_mobile_otp(data: schemas.MobileOTPVerify):
    try:
        res = config.auth_tokens_table.get_item(Key={"email": data.email, "purpose": "mobile_otp"})
        item = res.get("Item")

        if not item or item["token"] != data.otp:
            raise HTTPException(status_code=400, detail="Invalid or expired OTP")

        if config.users_table.get_item(Key={"email": data.email}).get("Item"):
            config.users_table.update_item(
                Key={"email": data.email},
                UpdateExpression="SET phoneVerified = :v",
                ExpressionAttributeValues={":v": True}
            )
        elif config.doctors_table.get_item(Key={"email": data.email}).get("Item"):
            config.doctors_table.update_item(
                Key={"email": data.email},
                UpdateExpression="SET phoneVerified = :v",
                ExpressionAttributeValues={":v": True}
            )

        config.auth_tokens_table.delete_item(Key={"email": data.email, "purpose": "mobile_otp"})
        return {"message": "Mobile number verified successfully"}

    except Exception:
        logger.exception("[VerifyMobileOTP] Error verifying mobile OTP")
        raise HTTPException(status_code=500, detail="Internal error verifying OTP")
