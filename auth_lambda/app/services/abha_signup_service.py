"""
ABHA → Kokoro signup.

Provisions a *loginable* Kokoro user from an existing ABHA account and links the
two together. Lives in auth_lambda (not abha_lambda) so it can reuse the
canonical user-creation path — Users record + AuthTable record + JWT — meaning
the provisioned user can immediately log in via POST /auth/login.

Input:  { abha_number, hospital_id }   (no phone needed — sourced from ABHA)
Flow:
  1. Read the AbhaAccounts row by abha_number (auth_lambda has the table handle).
  2. Resolve the phone from the ABHA record's `mobile` — this is the login
     credential (AuthTable PK). Login is currently passwordless: the mobile
     number alone authenticates (see handle_login), so no password is stored.
  3. Link-or-create:
       • ABHA already linked to a user_id        → reuse it (idempotent)
       • a Kokoro user with that phone exists     → reuse it
       • nobody matches                           → create a new user
  4. Ensure the AuthTable record (role=user, user_id, phone_verified) so the
     user can log in.
  5. Write kokoro_user_id back onto the AbhaAccounts row.
  6. Return a JWT so the caller is logged in straight away.
"""
from datetime import datetime, timezone

from fastapi import HTTPException

from app import config
from app.logger import get_logger
from app.utils.db_utils import normalize_phone_number
from app.utils.jwt_utils import create_jwt
from app.services.user_service import (
    create_user_profile,
    get_user_by_phone,
    get_user_by_id,
)
from app.services.auth_service import ensure_auth_record, update_auth_record
from app.services.membership_service import link_user_hospital

logger = get_logger(__name__)


def _get_abha_record(abha_number: str) -> dict:
    resp = config.abha_accounts_table.get_item(Key={"abha_number": abha_number})
    record = resp.get("Item")
    if not record:
        raise HTTPException(
            status_code=404,
            detail=f"No ABHA record found for {abha_number}. Create the ABHA first.",
        )
    return record


def _link_kokoro_user(abha_number: str, user_id: str) -> None:
    """Attach kokoro_user_id to the AbhaAccounts row (populates the GSI)."""
    try:
        config.abha_accounts_table.update_item(
            Key={"abha_number": abha_number},
            UpdateExpression="SET kokoro_user_id = :uid",
            ExpressionAttributeValues={":uid": user_id},
        )
        logger.info("[AbhaSignup] Linked kokoro_user_id=%s to abha_number=%s", user_id, abha_number)
    except Exception:
        # Non-fatal: the user/auth records are already created; log and continue.
        logger.exception("[AbhaSignup] Failed to link kokoro_user_id for abha_number=%s", abha_number)


def _mark_auth_account(normalized_phone: str, user_id: str) -> None:
    """Create/refresh the AuthTable record so the user can log in."""
    ensure_auth_record(normalized_phone, None)
    now_iso = datetime.now(timezone.utc).isoformat()
    update_auth_record(
        normalized_phone,
        {
            "role": "user",
            "user_id": user_id,
            "doctor_id": None,
            "is_verified": True,
            "phone_verified": True,  # phone is the credential; ABHA already verified identity
            "email_verified": False,
            "last_login": now_iso,
            "updated_at": now_iso,
        },
    )


def signup_user_from_abha(abha_number: str, hospital_id: str) -> dict:
    """
    Provision (or look up) a loginable Kokoro user for the given ABHA account.

    Returns:
        {
          "message": ...,
          "access_token": "<jwt>",
          "user_id": "usr_...",
          "abha_number": "...",
          "hospital_id": "...",
          "created": bool,          # a new Users record was written
          "already_linked": bool,   # the ABHA row already had a user_id
        }

    Raises:
        404 if no AbhaAccounts row exists for abha_number
        400 if the ABHA record has no mobile (cannot create a loginable user)
    """
    record = _get_abha_record(abha_number)

    # (1) Already linked → idempotent: refresh auth record, return a fresh JWT.
    existing_user_id = record.get("kokoro_user_id")
    if existing_user_id:
        user = get_user_by_id(existing_user_id)
        phone = (user or {}).get("phoneNumber") or normalize_phone_number(record.get("mobile") or "")
        if phone:
            _mark_auth_account(phone, existing_user_id)
        # Additive: link this user to the requesting hospital (may differ from a prior one).
        link_user_hospital(existing_user_id, hospital_id, source="abha")
        token = create_jwt(phone_number=phone or "", role="user", user_id=existing_user_id)
        logger.info("[AbhaSignup] ABHA %s already linked to user_id=%s", abha_number, existing_user_id)
        return {
            "message": "User already provisioned for this ABHA.",
            "access_token": token,
            "user_id": existing_user_id,
            "abha_number": abha_number,
            "hospital_id": hospital_id,
            "created": False,
            "already_linked": True,
        }

    # (2) Resolve the phone — required, it is the login credential.
    normalized_phone = normalize_phone_number(record.get("mobile") or "")
    if not normalized_phone:
        raise HTTPException(
            status_code=400,
            detail=(
                "ABHA record has no verified mobile. Verify the ABHA mobile "
                "(abha_lambda Flow A2) before signing up to Kokoro."
            ),
        )

    email = (record.get("email") or "").lower().strip() or None
    name = (record.get("full_name") or "").strip() or None

    # (3) Link-or-create — never duplicate an existing Kokoro user.
    user = get_user_by_phone(normalized_phone)
    created = False

    if user:
        user_id = user["user_id"]
        logger.info("[AbhaSignup] Reusing existing user_id=%s for ABHA %s", user_id, abha_number)
    else:
        user_item = create_user_profile(
            {"name": name, "email": email},
            normalized_phone,
            extra_fields={
                "source": "abha",
                "abha_linked": True,
                "abha_number": abha_number,
                "hospital_id": hospital_id,
                "gender": record.get("gender"),
                "dob": record.get("dob"),
                "address": record.get("address"),
                "pin_code": record.get("pin_code"),
            },
        )
        user_id = user_item["user_id"]
        created = True
        logger.info("[AbhaSignup] Created user_id=%s for ABHA %s", user_id, abha_number)

    # (4) AuthTable record so the user can log in.
    _mark_auth_account(normalized_phone, user_id)

    # (4b) Link the user to the requesting hospital (additive M:N; reused users
    #      may already belong to other hospitals — those are left intact).
    link_user_hospital(user_id, hospital_id, source="abha")

    # (5) Link back onto the ABHA row.
    _link_kokoro_user(abha_number, user_id)

    # (6) Issue a JWT — caller is now logged in.
    token = create_jwt(phone_number=normalized_phone, role="user", user_id=user_id)

    return {
        "message": "User provisioned from ABHA successfully.",
        "access_token": token,
        "user_id": user_id,
        "abha_number": abha_number,
        "hospital_id": hospital_id,
        "created": created,
        "already_linked": False,
    }
