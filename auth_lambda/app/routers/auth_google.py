from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from google.auth.transport import requests as grequests
from google.oauth2 import id_token

from app import config
from app.logger import get_logger
from app.utils.db_utils import (
    generate_user_id,
    normalize_phone_number,
)
from app.services.user_service import get_user_by_email
from app.services.auth_service import ensure_auth_record, update_auth_record
from app.utils.jwt_utils import create_jwt

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["google-auth"])

@router.post("/google")
def google_auth(data: dict):
    # small defensive check (data is expected to have 'token')
    token = data.get("token")
    logger.info(f"[GoogleAuth] Request received - token_exists: {bool(token)}")
    if not token:
        raise HTTPException(status_code=400, detail="Missing token")

    try:
        idinfo = id_token.verify_oauth2_token(token, grequests.Request(), config.CLIENT_IDS)
        email = idinfo.get("email")
        name = idinfo.get("name", "") or idinfo.get("given_name", "")
        picture = idinfo.get("picture", "")

        if not email:
            logger.error("[GoogleAuth] No email found in Google response")
            raise HTTPException(status_code=400, detail="No email found in Google response")

        logger.info(f"[GoogleAuth] Verified Google user -> {email}")

        user = get_user_by_email(email)
        now_iso = datetime.now(timezone.utc).isoformat()

        if not user:
            user_id = generate_user_id()
            user_name = name or "Unknown User"
            user = {
                "user_id": user_id,
                "name": user_name,
                "username": user_name,
                "email": email,
                "phoneNumber": None,
                "picture": picture,
                "authProvider": "google",
                "emailVerified": True,
                "createdAt": now_iso,
            }
            config.users_table.put_item(Item=user)
            logger.info(f"[GoogleAuth] New user created with ID: {user_id}")
        else:
            user_id = user.get("user_id")
            update_expr_parts = []
            expr_attr_values = {}

            if not user.get("name") and name:
                update_expr_parts.append("name = :name")
                expr_attr_values[":name"] = name
            if not user.get("username") and name:
                update_expr_parts.append("username = :username")
                expr_attr_values[":username"] = name
            if picture and user.get("picture") != picture:
                update_expr_parts.append("picture = :pic")
                expr_attr_values[":pic"] = picture
            if user.get("authProvider") != "google":
                update_expr_parts.append("authProvider = :auth")
                expr_attr_values[":auth"] = "google"

            if update_expr_parts:
                config.users_table.update_item(
                    Key={"user_id": user_id},
                    UpdateExpression="SET " + ", ".join(update_expr_parts),
                    ExpressionAttributeValues=expr_attr_values
                )
                # refresh local copy
                user = get_user_by_email(email) or user

        phone_number = user.get("phoneNumber")
        normalized_phone = (
            normalize_phone_number(phone_number) if phone_number else None
        )

        if normalized_phone:
            ensure_auth_record(normalized_phone)
            update_auth_record(
                normalized_phone,
                {
                    "role": "user",
                    "user_id": user["user_id"],
                    "is_verified": True,
                    "last_login": now_iso,
                    "updated_at": now_iso,
                },
            )
        else:
            logger.info("[GoogleAuth] No phone number on record for %s", email)

        token_phone = normalized_phone or f"google:{user['user_id']}"
        access_token = create_jwt(
            phone_number=token_phone,
            role="user",
            user_id=user["user_id"],
        )

        profile = {
            "user_id": user["user_id"],
            "name": user.get("name") or user.get("username"),
            "email": user.get("email"),
            "phoneNumber": normalized_phone,
            "picture": user.get("picture") or picture,
        }

        logger.info("[GoogleAuth] Returning response for %s", email)
        return {
            "profile": profile,
            "access_token": access_token,
            "role": "user",
            "message": "Login successful via Google",
        }

    except Exception as e:
        logger.exception("[GoogleAuth] Exception during auth")
        raise HTTPException(status_code=400, detail=f"Google auth failed: {str(e)}")
