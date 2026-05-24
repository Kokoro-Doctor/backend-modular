"""
AbhaAccounts DynamoDB service.

Table: AbhaAccounts
PK:    abha_number  (e.g. "91-4118-0337-7265")
GSI:   kokoro_user_id-index  (lookup by Kokoro user_id)

Responsibilities
────────────────
1. save()                        — write full profile + tokens after create / login
2. get_valid_token()             — return a live access token by abha_number
3. get_valid_token_by_kokoro_user() — same but resolved from kokoro_user_id via GSI
4. get()                         — raw record lookup (for debugging / future use)
5. get_by_kokoro_user_id()       — GSI lookup

Token strategy
──────────────
We store absolute expiry ISO timestamps (not durations) so any Lambda
instance at any time can compare datetime.now() < expiry without needing
to know when the token was originally issued.

  access_token_expiry   → now + expiresIn seconds
  refresh_token_expiry  → now + refreshExpiresIn seconds  (~15 days)

Refresh flow in get_valid_token():
  1. access token still valid (60s buffer) → return it immediately
  2. access token expired, refresh token still valid
       → call ABDM refresh endpoint
       → persist new tokens
       → return new access token
  3. both expired → 401, user must OTP again
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

from app import config
from app.abdm.schemas import ABHAProfile, ABDMTokens
from app.logger import get_logger

logger = get_logger(__name__)

_EXPIRY_BUFFER_SECONDS = 60   # treat token as expired this many seconds early


# ---------------------------------------------------------------------------
# Write — called after create or login
# ---------------------------------------------------------------------------

def save(profile: ABHAProfile, tokens: ABDMTokens, kokoro_user_id: Optional[str] = None) -> None:
    """
    Upsert a full ABHA record into AbhaAccounts.
    Called after both creation and login — always has fresh tokens.
    Pass kokoro_user_id to link this ABHA to a Kokoro account (populates GSI).
    """
    if not profile.ABHANumber:
        logger.warning("[AbhaAccountsService] save() called with no ABHANumber — skipping")
        return

    now = datetime.now(timezone.utc)

    # Build the fields to update — strip None values (DynamoDB rejects them)
    fields = {
        "abha_address":         profile.preferredAbhaAddress or profile.preferredAddress,
        "first_name":           profile.firstName,
        "middle_name":          profile.middleName,
        "last_name":            profile.lastName,
        "full_name":            profile.name or _build_full_name(profile),
        "dob":                  profile.dob or _build_dob(profile),
        "gender":               profile.gender,
        "mobile":               profile.mobile,
        "mobile_verified":      profile.mobileVerified,
        "email":                profile.email,
        "phr_addresses":        profile.phrAddress,
        "address":              profile.address,
        "state_code":           profile.stateCode,
        "state_name":           profile.stateName,
        "district_code":        profile.districtCode,
        "district_name":        profile.districtName,
        "pin_code":             profile.pinCode or profile.pincode,
        "profile_photo":        profile.photo or profile.profilePhoto,
        "abha_status":          profile.abhaStatus or profile.status,
        "abha_type":            getattr(profile, "abhaType", None),
        "verification_status":  profile.verificationStatus,
        "access_token":         tokens.token,
        "access_token_expiry":  (now + timedelta(seconds=tokens.expiresIn)).isoformat(),
        "refresh_token":        tokens.refreshToken,
        "refresh_token_expiry": (now + timedelta(seconds=tokens.refreshExpiresIn)).isoformat(),
        "last_synced_at":       now.isoformat(),
    }
    fields = {k: v for k, v in fields.items() if v is not None}

    # Only write kokoro_user_id when provided — never overwrite an existing link
    # with None just because the user didn't send a Kokoro JWT this time.
    if kokoro_user_id:
        fields["kokoro_user_id"] = kokoro_user_id

    # update_item (not put_item) — fields not in this call are left untouched.
    # created_at is set only on first write via attribute_not_exists.
    _run_update(profile.ABHANumber, fields, set_created_at_if_new=now.isoformat())
    logger.info(
        "[AbhaAccountsService] Saved record for abha_number=%s kokoro_user_id=%s",
        profile.ABHANumber, kokoro_user_id,
    )


def update_profile(abha_number: str, profile: ABHAProfile) -> None:
    """
    Refresh only profile fields (no token fields touched).
    Called after a live GET /profile/account to keep the record fresh.
    """
    now = datetime.now(timezone.utc)
    fields = {
        "abha_status":    profile.abhaStatus or profile.status,
        "full_name":      profile.name or _build_full_name(profile),
        "mobile":         profile.mobile,
        "email":          profile.email,
        "address":        profile.address,
        "last_synced_at": now.isoformat(),
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    _run_update(abha_number, fields)


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

def get_valid_token(abha_number: str) -> str:
    """
    Return a valid ABDM access token for abha_number.
    Auto-refreshes via the stored refresh_token if the access token has expired.

    Raises:
        404  if no record found for abha_number
        401  if both tokens are expired (user must OTP again)
        502  if the ABDM refresh call fails
    """
    record = get(abha_number)
    if not record:
        raise HTTPException(
            status_code=404,
            detail=f"No ABHA record found for {abha_number}. Please create or login first.",
        )
    return _resolve_token(record, abha_number)


def get_valid_token_by_kokoro_user(kokoro_user_id: str) -> str:
    """
    Return a valid ABDM access token resolved by Kokoro user_id (via GSI).
    Used when the caller sends a Kokoro JWT instead of an ABHA number.

    Raises:
        404  if this Kokoro user has no linked ABHA account
        401  if both tokens are expired (user must OTP again)
    """
    record = get_by_kokoro_user_id(kokoro_user_id)
    if not record:
        raise HTTPException(
            status_code=404,
            detail="No ABHA account linked to your Kokoro account. Please create or login to ABHA first.",
        )
    abha_number = record.get("abha_number", "unknown")
    return _resolve_token(record, abha_number)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get(abha_number: str) -> Optional[dict]:
    """Return the raw DynamoDB item for abha_number, or None if not found."""
    resp = config.abha_table.get_item(Key={"abha_number": abha_number})
    return resp.get("Item")


def get_by_kokoro_user_id(kokoro_user_id: str) -> Optional[dict]:
    """Query the GSI to find the ABHA record linked to a Kokoro user_id."""
    resp = config.abha_table.query(
        IndexName="kokoro_user_id-index",
        KeyConditionExpression="kokoro_user_id = :uid",
        ExpressionAttributeValues={":uid": kokoro_user_id},
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _resolve_token(record: dict, abha_number: str) -> str:
    """
    Given a raw DynamoDB record, return a valid access token.
    Refreshes automatically if the access token is expired.
    """
    access_token          = record.get("access_token")
    access_token_expiry   = record.get("access_token_expiry")
    refresh_token         = record.get("refresh_token")
    refresh_token_expiry  = record.get("refresh_token_expiry")

    now = datetime.now(timezone.utc)

    if access_token and _is_valid(access_token_expiry, now):
        logger.debug("[AbhaAccountsService] Access token valid for %s", abha_number)
        return access_token

    logger.info("[AbhaAccountsService] Access token expired for %s, refreshing", abha_number)

    if not refresh_token:
        raise HTTPException(status_code=401, detail="ABHA session expired. Please login again.")

    if not _is_valid(refresh_token_expiry, now):
        raise HTTPException(
            status_code=401,
            detail="ABHA session fully expired (15-day limit). Please login again.",
        )

    from app.services.abha_service import refresh_user_token
    new_tokens = refresh_user_token(refresh_token)

    _run_update(abha_number, {
        "access_token":         new_tokens.token,
        "access_token_expiry":  (now + timedelta(seconds=new_tokens.expiresIn)).isoformat(),
        "refresh_token":        new_tokens.refreshToken,
        "refresh_token_expiry": (now + timedelta(seconds=new_tokens.refreshExpiresIn)).isoformat(),
        "last_synced_at":       now.isoformat(),
    })

    logger.info("[AbhaAccountsService] Tokens refreshed for %s", abha_number)
    return new_tokens.token


def _is_valid(expiry_iso: Optional[str], now: datetime) -> bool:
    """True if expiry is in the future (with buffer), False if missing or past."""
    if not expiry_iso:
        return False
    try:
        dt = datetime.fromisoformat(expiry_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return now < dt - timedelta(seconds=_EXPIRY_BUFFER_SECONDS)
    except (ValueError, TypeError):
        return False


def _run_update(abha_number: str, fields: dict, set_created_at_if_new: Optional[str] = None) -> None:
    set_parts = [f"#{k} = :{k}" for k in fields]
    names     = {f"#{k}": k for k in fields}
    values    = {f":{k}": v for k, v in fields.items()}

    # On first write, also set created_at — but never overwrite it on subsequent writes.
    if set_created_at_if_new:
        set_parts.append("#created_at = if_not_exists(#created_at, :created_at)")
        names["#created_at"]  = "created_at"
        values[":created_at"] = set_created_at_if_new

    expr = "SET " + ", ".join(set_parts)

    config.abha_table.update_item(
        Key={"abha_number": abha_number},
        UpdateExpression=expr,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def _build_full_name(profile: ABHAProfile) -> Optional[str]:
    parts = [profile.firstName, profile.middleName, profile.lastName]
    name = " ".join(p for p in parts if p)
    return name or None


def _build_dob(profile: ABHAProfile) -> Optional[str]:
    if profile.dayOfBirth and profile.monthOfBirth and profile.yearOfBirth:
        return f"{profile.dayOfBirth}-{profile.monthOfBirth}-{profile.yearOfBirth}"
    return None
