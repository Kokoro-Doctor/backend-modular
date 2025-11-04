from fastapi import APIRouter, HTTPException
from google.oauth2 import id_token
from google.auth.transport import requests as grequests
from app import config
from app.utils.tokens import generate_token
from datetime import datetime, timezone
from app.logger import get_logger

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

        # check user in DB
        resp = config.users_table.get_item(Key={"email": email})
        user = resp.get("Item")
        logger.debug(f"[GoogleAuth] DB response: {resp}")

        if not user:
            # create new user
            user = {
                "username": name or "Unknown User",
                "email": email,
                "password": None,
                "phoneNumber": None,
                "location": None,
                "emailVerified": True,
                "phoneVerified": False,
                "picture": picture,
                "authProvider": "google",
                "createdAt": datetime.now(timezone.utc).isoformat()
            }
            config.users_table.put_item(Item=user)
            logger.info("[GoogleAuth] New user created")
        else:
            # update existing
            needs_update = False
            if not user.get("picture") and picture:
                user["picture"] = picture
                needs_update = True
            if not user.get("authProvider"):
                user["authProvider"] = "password"
            if "google" not in user.get("authProvider", ""):
                user["authProvider"] = user["authProvider"] + "+google"
            if needs_update:
                config.users_table.put_item(Item=user)
                logger.info("[GoogleAuth] Updated user with google data")

        # issue app token (UUID)
        access_token = generate_token()
        response_data = {
            "user": {
                "name": user.get("username", "Unknown User"),
                "email": user["email"],
                "picture": user.get("picture") or picture,
                "authProvider": user.get("authProvider", "unknown")
            },
            "access_token": access_token
        }
        logger.info("[GoogleAuth] Returning response")
        return response_data

    except Exception as e:
        logger.exception("[GoogleAuth] Exception during auth")
        raise HTTPException(status_code=400, detail=f"Google auth failed: {str(e)}")
