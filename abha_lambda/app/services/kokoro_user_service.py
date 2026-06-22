"""
Provision a Kokoro website user from an ABHA record.

Flow (triggered AFTER ABHA creation, once the AbhaAccounts row exists):
  1. Read the AbhaAccounts row by abha_number.
  2. If it is already linked to a Kokoro user_id  → return that user (idempotent).
  3. Otherwise resolve phone/email/name from the ABHA record (callers may
     override any of these in the request).
  4. Link-or-create:
       • a Kokoro user with that phone (or email) already exists → reuse it
       • nobody matches                                          → create a new
         Users-table record
  5. Write kokoro_user_id back onto the AbhaAccounts row (populates the
     kokoro_user_id-index GSI).

IMPORTANT — login caveat
────────────────────────
This intentionally creates ONLY the Users-table profile (per product decision).
It does NOT create an AuthTable record, so the provisioned user CANNOT log in
via phone/email OTP until an AuthTable record (phoneNumber PK, role=user,
user_id, is_verified/phone_verified) is created for them — that is auth_lambda's
job. Until then the account exists for data-linkage purposes only.
"""
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from boto3.dynamodb.conditions import Key
from fastapi import HTTPException

from app import config
from app.logger import get_logger
from app.services import abha_accounts_service

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Local helpers (kept in-module to avoid importing across lambda boundaries)
# ---------------------------------------------------------------------------

def _generate_user_id() -> str:
    """Match auth_lambda's user_id shape: 'usr_<uuid4>'."""
    return f"usr_{uuid.uuid4()}"


def _normalize_phone(phone: Optional[str]) -> str:
    """
    Normalize to E.164. Preserves an existing '+<cc>' prefix; otherwise assumes
    an Indian number and prepends config.SMS_COUNTRY_CODE. Returns "" if the
    input has no usable digits. Mirrors auth_lambda.normalize_phone_number's
    common cases (ABHA mobiles are bare 10-digit Indian numbers).
    """
    if not phone:
        return ""
    trimmed = phone.strip()
    digits = re.sub(r"\D", "", trimmed)
    if not digits:
        return ""
    if trimmed.startswith("+"):
        return "+" + digits
    last_10 = digits[-10:]
    if len(last_10) != 10:
        return ""
    return f"{config.SMS_COUNTRY_CODE}{last_10}"


def _get_user_by_phone(normalized_phone: str) -> Optional[dict]:
    if not normalized_phone:
        return None
    try:
        resp = config.users_table.query(
            IndexName="phone-index",
            KeyConditionExpression=Key("phoneNumber").eq(normalized_phone),
        )
        items = resp.get("Items", [])
        return items[0] if items else None
    except Exception:
        logger.exception("[KokoroUserService] phone lookup failed for %s", normalized_phone)
        return None


def _get_user_by_email(email: str) -> Optional[dict]:
    if not email:
        return None
    try:
        resp = config.users_table.query(
            IndexName="email-index",
            KeyConditionExpression=Key("email").eq(email.lower().strip()),
        )
        items = resp.get("Items", [])
        return items[0] if items else None
    except Exception:
        logger.exception("[KokoroUserService] email lookup failed for %s", email)
        return None


def _get_user_by_id(user_id: str) -> Optional[dict]:
    try:
        resp = config.users_table.get_item(Key={"user_id": user_id})
        return resp.get("Item")
    except Exception:
        logger.exception("[KokoroUserService] get_item failed for %s", user_id)
        return None


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def signup_user_from_abha(abha_number: str, hospital_id: str) -> dict:
    """
    Provision (or look up) a Kokoro user for the given ABHA account and link
    them together. Identity (phone/email/name) is taken entirely from the
    AbhaAccounts record. hospital_id is stored on the Users record so the user
    is linked to the originating hospital.

    Returns:
        {
          "user_id":        "usr_...",
          "abha_number":    "91-....",
          "created":        bool,   # True only when a NEW Users record was written
          "already_linked": bool,   # True when the ABHA row already had a user_id
        }

    Raises:
        404 if no AbhaAccounts row exists for abha_number
        400 if no phone or email can be resolved (nothing to identify the user)
    """
    record = abha_accounts_service.get(abha_number)
    if not record:
        raise HTTPException(
            status_code=404,
            detail=f"No ABHA record found for {abha_number}. Create the ABHA first.",
        )

    # (2) Idempotent — already linked
    existing_user_id = record.get("kokoro_user_id")
    if existing_user_id:
        logger.info(
            "[KokoroUserService] ABHA %s already linked to user_id=%s",
            abha_number, existing_user_id,
        )
        return {
            "user_id": existing_user_id,
            "abha_number": abha_number,
            "created": False,
            "already_linked": True,
        }

    # (3) Resolve identity from the ABHA record
    normalized_phone = _normalize_phone(record.get("mobile"))
    resolved_email = (record.get("email") or "").lower().strip() or None
    resolved_name = (record.get("full_name") or "").strip() or None

    if not normalized_phone and not resolved_email:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cannot sign up: ABHA record has no mobile or email. "
                "Verify the ABHA mobile first (Flow A2) before signing up."
            ),
        )

    # (4) Link-or-create — never duplicate an existing Kokoro user
    user = _get_user_by_phone(normalized_phone) or _get_user_by_email(resolved_email)
    created = False

    if user:
        user_id = user["user_id"]
        logger.info(
            "[KokoroUserService] Reusing existing Kokoro user_id=%s for ABHA %s",
            user_id, abha_number,
        )
    else:
        user_id = _generate_user_id()
        now_iso = datetime.now(timezone.utc).isoformat()
        user_item = {
            "user_id": user_id,
            "createdAt": now_iso,
            # Provenance + reverse links
            "source": "abha",
            "abha_linked": True,
            "abha_number": abha_number,
            "hospital_id": hospital_id,
        }
        if normalized_phone:
            user_item["phoneNumber"] = normalized_phone
        if resolved_email:
            user_item["email"] = resolved_email
        if resolved_name:
            user_item["name"] = resolved_name

        try:
            config.users_table.put_item(Item=user_item)
        except Exception:
            logger.exception("[KokoroUserService] Failed to create Users record for ABHA %s", abha_number)
            raise HTTPException(status_code=500, detail="Failed to create Kokoro user profile")

        created = True
        logger.info("[KokoroUserService] Created Kokoro user_id=%s for ABHA %s", user_id, abha_number)

    # (5) Link the user back onto the AbhaAccounts row (best-effort but important)
    abha_accounts_service.link_kokoro_user(abha_number, user_id)

    return {
        "user_id": user_id,
        "abha_number": abha_number,
        "created": created,
        "already_linked": False,
    }
