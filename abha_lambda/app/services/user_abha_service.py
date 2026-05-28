"""
User ABHA data — DynamoDB Users table read/write.

Token storage strategy
──────────────────────
We store TWO timestamps (not durations) for each token:

  abha_user_token_expiry          → ISO UTC timestamp when the access token expires
  abha_user_refresh_token_expiry  → ISO UTC timestamp when the refresh token expires

Why timestamps not durations?
  Storing expiresIn=1800 tells you nothing at read time — you don't know
  when the 1800s started. Storing the absolute expiry as an ISO string lets
  any Lambda instance, at any time, just compare datetime.now() < expiry.

Refresh flow (get_valid_user_token)
────────────────────────────────────
  1. Read user from DynamoDB (caller's responsibility — user dict is passed in)
  2. Is abha_user_token_expiry still in the future (+60s buffer)? → use it, done
  3. Expired → check abha_user_refresh_token_expiry
  4. Refresh token also expired → raise 401, user must log in again
  5. Refresh token valid → call ABDM refresh endpoint → get new tokens
  6. Write new tokens + expiry timestamps to DynamoDB (best-effort, non-fatal)
  7. Return new access token
"""

import base64
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

from app import config
from app.abdm.schemas import ABHAProfile, ABDMTokens
from app.logger import get_logger

logger = get_logger(__name__)

# How many seconds before actual expiry we consider the token "expired"
# so we never use a token that's about to expire mid-flight
_EXPIRY_BUFFER_SECONDS = 60


# ---------------------------------------------------------------------------
# Primary write — called after creation or login (always has fresh tokens)
# ---------------------------------------------------------------------------

def update_user_abha(
    user_id: str,
    profile: ABHAProfile,
    tokens: ABDMTokens,
    verification_type: str = "AADHAAR_OTP",
) -> None:
    """
    Write all ABHA profile fields + tokens onto the existing Users table item.
    Stores absolute expiry timestamps so any Lambda instance can check validity.
    Called after both ABHA creation and ABHA login verification.
    """
    now = datetime.now(timezone.utc)

    fields = {
        "abha_linked": True,
        "abha_verified": True,
        "abha_number": profile.ABHANumber,
        "abha_address": profile.preferredAbhaAddress or profile.preferredAddress,
        "abha_status": profile.status or profile.abhaStatus,
        "abha_first_name": profile.firstName,
        "abha_middle_name": profile.middleName,
        "abha_last_name": profile.lastName,
        "abha_full_name": profile.name or _build_full_name(profile),
        "abha_dob": profile.dob or _build_dob(profile),
        "abha_gender": profile.gender,
        "abha_mobile": profile.mobile,
        "abha_mobile_verified": profile.mobileVerified,
        "abha_email": profile.email,
        "abha_address_text": profile.address,
        "abha_state_code": profile.stateCode,
        "abha_state_name": profile.stateName,
        "abha_district_code": profile.districtCode,
        "abha_district_name": profile.districtName,
        "abha_pin_code": profile.pinCode or profile.pincode,
        "abha_profile_photo": profile.photo or profile.profilePhoto,
        "abha_created_date": profile.createdDate,
        "abha_verification_status": profile.verificationStatus or "VERIFIED",
        "abha_verification_type": verification_type,
        "abha_auth_methods": profile.authMethods,
        # Tokens — store value + absolute expiry timestamp
        "abha_user_token": tokens.token,
        "abha_user_token_expiry": (now + timedelta(seconds=tokens.expiresIn)).isoformat(),
        "abha_user_refresh_token": tokens.refreshToken,
        "abha_user_refresh_token_expiry": (now + timedelta(seconds=tokens.refreshExpiresIn)).isoformat(),
        "abha_last_synced_at": now.isoformat(),
    }

    if profile.phrAddress:
        fields["abha_phr_addresses"] = profile.phrAddress
        if profile.preferredAbhaAddress:
            fields["abha_preferred_address"] = profile.preferredAbhaAddress
        elif len(profile.phrAddress) == 1:
            fields["abha_preferred_address"] = profile.phrAddress[0]

    # Don't overwrite existing fields with None
    fields = {k: v for k, v in fields.items() if v is not None}

    _run_update(user_id, fields, context="update_user_abha")
    logger.info("[UserABHAService] ABHA data saved for user_id=%s", user_id)


# ---------------------------------------------------------------------------
# Profile-only sync — called after GET /profile (no new tokens issued)
# ---------------------------------------------------------------------------

def sync_abha_profile(user_id: str, profile: ABHAProfile) -> None:
    """
    Update only profile fields in DynamoDB — does NOT touch token fields.
    Called when we re-fetch the profile to keep it fresh without overwriting
    a valid (possibly freshly refreshed) token with stale data.
    """
    now = datetime.now(timezone.utc)

    fields = {
        "abha_status": profile.status or profile.abhaStatus,
        "abha_first_name": profile.firstName,
        "abha_middle_name": profile.middleName,
        "abha_last_name": profile.lastName,
        "abha_full_name": profile.name or _build_full_name(profile),
        "abha_gender": profile.gender,
        "abha_mobile": profile.mobile,
        "abha_mobile_verified": profile.mobileVerified,
        "abha_email": profile.email,
        "abha_address_text": profile.address,
        "abha_state_code": profile.stateCode,
        "abha_state_name": profile.stateName,
        "abha_district_code": profile.districtCode,
        "abha_district_name": profile.districtName,
        "abha_pin_code": profile.pinCode or profile.pincode,
        "abha_profile_photo": profile.profilePhoto or profile.photo,
        "abha_auth_methods": profile.authMethods,
        "abha_verification_status": profile.verificationStatus,
        "abha_last_synced_at": now.isoformat(),
    }

    fields = {k: v for k, v in fields.items() if v is not None}
    _run_update(user_id, fields, context="sync_abha_profile")
    logger.info("[UserABHAService] Profile synced for user_id=%s", user_id)


# ---------------------------------------------------------------------------
# Token refresh — the core of this module
# ---------------------------------------------------------------------------

def get_valid_user_token(user_id: str, user: dict) -> str:
    """
    Return a valid ABDM user access token, auto-refreshing if it has expired.

    Decision tree:
      ┌─ access token present? ──No──→ 400 (ABHA not linked)
      │
      ├─ access token still valid? ──Yes──→ return it (fast path)
      │
      ├─ refresh token present? ──No──→ 401 (need to log in again)
      │
      ├─ refresh token expired? ──Yes──→ 401 (need to log in again)
      │
      └─ call ABDM refresh endpoint → persist new tokens → return new access token
    """
    access_token = user.get("abha_user_token")
    access_expiry_str = user.get("abha_user_token_expiry")
    refresh_token = user.get("abha_user_refresh_token")
    refresh_expiry_str = user.get("abha_user_refresh_token_expiry")

    if not access_token:
        raise HTTPException(
            status_code=400,
            detail="ABHA not linked. Complete ABHA creation or login first.",
        )

    now = datetime.now(timezone.utc)

    # ── Fast path: access token still valid ──────────────────────────────────
    if access_expiry_str and _is_token_valid(access_expiry_str, now):
        logger.debug("[UserABHAService] Access token valid for user_id=%s", user_id)
        return access_token

    # ── Access token expired — attempt refresh ────────────────────────────────
    logger.info("[UserABHAService] Access token expired, refreshing for user_id=%s", user_id)

    if not refresh_token:
        raise HTTPException(
            status_code=401,
            detail="ABHA session expired. Please log in to ABHA again.",
        )

    if refresh_expiry_str and not _is_token_valid(refresh_expiry_str, now):
        logger.warning(
            "[UserABHAService] Refresh token also expired for user_id=%s", user_id
        )
        raise HTTPException(
            status_code=401,
            detail="ABHA session fully expired (refresh token expired). Please log in to ABHA again.",
        )

    # ── Call ABDM refresh endpoint ────────────────────────────────────────────
    # Import here to avoid circular dependency (abha_service imports this module indirectly)
    from app.services.abha_service import refresh_user_token

    new_tokens = refresh_user_token(refresh_token)
    _save_refreshed_tokens(user_id, new_tokens, now)
    logger.info("[UserABHAService] Tokens refreshed for user_id=%s", user_id)
    return new_tokens.token


# ---------------------------------------------------------------------------
# S3 — ABHA card storage
# ---------------------------------------------------------------------------

def save_abha_card_to_s3(user_id: str, pdf_bytes: bytes) -> str:
    """
    Upload the ABHA card PDF to S3 and persist the S3 key on the Users item.
    Returns the S3 key (not a URL — URLs are generated on demand via presigned).
    """
    key = f"{config.ABHA_CARD_PREFIX}{user_id}.pdf"
    try:
        config.s3_client.put_object(
            Bucket=config.S3_BUCKET,
            Key=key,
            Body=pdf_bytes,
            ContentType="application/pdf",
        )
        logger.info("[UserABHAService] ABHA card uploaded s3://%s/%s", config.S3_BUCKET, key)
    except Exception:
        logger.exception("[UserABHAService] Failed to upload ABHA card for user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="Failed to store ABHA card")

    try:
        config.users_table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET #k = :k",
            ExpressionAttributeNames={"#k": "abha_card_s3_key"},
            ExpressionAttributeValues={":k": key},
        )
    except Exception:
        logger.warning("[UserABHAService] Card uploaded but key not persisted for user_id=%s", user_id)

    return key


def get_abha_card_presigned_url(user_id: str, expires_in: int = 3600) -> Optional[str]:
    """Generate a time-limited presigned URL for the stored ABHA card PDF."""
    key = f"{config.ABHA_CARD_PREFIX}{user_id}.pdf"
    try:
        return config.s3_client.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": config.S3_BUCKET, "Key": key},
            ExpiresIn=expires_in,
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _is_token_valid(expiry_iso: str, now: datetime) -> bool:
    """Return True if the token expires more than BUFFER seconds from now."""
    try:
        expiry_dt = datetime.fromisoformat(expiry_iso)
        # Make sure expiry_dt is timezone-aware for a fair comparison
        if expiry_dt.tzinfo is None:
            expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
        return now < expiry_dt - timedelta(seconds=_EXPIRY_BUFFER_SECONDS)
    except (ValueError, TypeError):
        # Malformed timestamp — treat as expired, force a refresh
        return False


def _save_refreshed_tokens(user_id: str, tokens: ABDMTokens, now: datetime) -> None:
    """
    Persist only the token fields after a refresh.
    Non-fatal: if this DynamoDB write fails the caller already has the new token
    in hand and can proceed. The next request will refresh again (extra ~200ms).
    """
    fields = {
        "abha_user_token": tokens.token,
        "abha_user_token_expiry": (now + timedelta(seconds=tokens.expiresIn)).isoformat(),
        "abha_user_refresh_token": tokens.refreshToken,
        "abha_user_refresh_token_expiry": (now + timedelta(seconds=tokens.refreshExpiresIn)).isoformat(),
        "abha_last_synced_at": now.isoformat(),
    }
    try:
        _run_update(user_id, fields, context="_save_refreshed_tokens")
        logger.info("[UserABHAService] Refreshed tokens persisted for user_id=%s", user_id)
    except Exception:
        logger.warning(
            "[UserABHAService] Token refresh succeeded but DynamoDB write failed for user_id=%s"
            " — next request will refresh again",
            user_id,
        )


def _run_update(user_id: str, fields: dict, context: str = "") -> None:
    """Execute a DynamoDB update_item for the given field dict."""
    update_expr = "SET " + ", ".join(f"#{k} = :{k}" for k in fields)
    expr_names = {f"#{k}": k for k in fields}
    expr_values = {f":{k}": v for k, v in fields.items()}
    config.users_table.update_item(
        Key={"user_id": user_id},
        UpdateExpression=update_expr,
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )


def _build_full_name(profile: ABHAProfile) -> Optional[str]:
    parts = [profile.firstName, profile.middleName, profile.lastName]
    name = " ".join(p for p in parts if p)
    return name or None


def _build_dob(profile: ABHAProfile) -> Optional[str]:
    if profile.dayOfBirth and profile.monthOfBirth and profile.yearOfBirth:
        return f"{profile.dayOfBirth}-{profile.monthOfBirth}-{profile.yearOfBirth}"
    return None
