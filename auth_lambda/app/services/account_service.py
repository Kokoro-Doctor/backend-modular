"""
Account service - handles full account deletion business logic.

The public entry point is `delete_account_by_phone`. It resolves a user and/or
doctor identity from *any* surviving trace (the Users/Doctors profile row, or the
AuthTable identity record), then sweeps every per-account table and S3 prefix.

Design notes:
  * It never bails just because the Users/Doctors profile row is gone. As long as
    an AuthTable record, an auth token, or any other row keyed to the recovered
    user_id/doctor_id exists, that data is deleted. This makes the endpoint
    self-correcting for accounts that were partially deleted by hand.
  * Every table operation is isolated: a failure on one table is logged and
    reported in `errors` but does not stop the rest of the cleanup.
  * `account_found` is True if any identity row existed OR anything was actually
    deleted, so the caller only returns 404 when the phone is genuinely clean.
"""
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError

from app import config
from app.logger import get_logger
from app.utils.db_utils import normalize_phone_number
from app.services.user_service import get_user_by_phone_for_admin, get_user_by_email
from app.services.doctor_service import get_doctor_by_phone_for_admin

logger = get_logger(__name__)


def _get_auth_record(normalized_phone):
    """Fetch the AuthTable identity row by normalized phone (the canonical
    user_id/doctor_id/email link). Kept local to avoid importing auth_service."""
    try:
        resp = config.auth_table.get_item(Key={"phoneNumber": normalized_phone})
        return resp.get("Item")
    except Exception as e:
        logger.error(f"[delete-account] Error fetching auth record for {normalized_phone}: {e}")
        return None


# --------------------------------------------------------------------------- #
# Low-level helpers
# --------------------------------------------------------------------------- #
def _query_all(table, **kwargs):
    """Run a query, following pagination, and return all items."""
    items = []
    resp = table.query(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.query(ExclusiveStartKey=resp["LastEvaluatedKey"], **kwargs)
        items.extend(resp.get("Items", []))
    return items


def _scan_all(table, **kwargs):
    """Run a scan, following pagination, and return all items."""
    items = []
    resp = table.scan(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **kwargs)
        items.extend(resp.get("Items", []))
    return items


def _batch_delete(table, items, key_fn):
    """Delete every item using a key extracted by key_fn. Returns count deleted."""
    count = 0
    with table.batch_writer() as writer:
        for item in items:
            writer.delete_item(Key=key_fn(item))
            count += 1
    return count


def _delete_one(table, key):
    """Delete a single item by primary key. Returns 1 if it existed, else 0."""
    resp = table.delete_item(Key=key, ReturnValues="ALL_OLD")
    return 1 if resp.get("Attributes") else 0


def _delete_s3_prefix(prefix):
    """Delete every S3 object under a prefix. Returns count deleted."""
    count = 0
    paginator = config.s3_client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=config.S3_BUCKET, Prefix=prefix)
    for page in pages:
        objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if not objects:
            continue
        # delete_objects accepts up to 1000 keys per call
        for i in range(0, len(objects), 1000):
            config.s3_client.delete_objects(
                Bucket=config.S3_BUCKET,
                Delete={"Objects": objects[i:i + 1000], "Quiet": True},
            )
        count += len(objects)
    return count


def _safe(deleted, errors, label, fn):
    """Run a deletion step, recording its count or its error without raising."""
    try:
        deleted[label] = fn()
    except ClientError as e:
        # NoSuchKey on S3 is harmless (nothing to delete)
        if e.response.get("Error", {}).get("Code") == "NoSuchKey":
            deleted[label] = 0
            return
        logger.error(f"[delete-account] Error deleting {label}: {e}", exc_info=True)
        errors.append(f"{label}: {e}")
    except Exception as e:
        logger.error(f"[delete-account] Error deleting {label}: {e}", exc_info=True)
        errors.append(f"{label}: {e}")


# --------------------------------------------------------------------------- #
# Identity / phone / email scoped cleanup (shared by user & doctor)
# --------------------------------------------------------------------------- #
def _delete_identity_records(phone, email, deleted, errors):
    """Delete the phone-keyed AuthTable row and all AuthTokens for phone/email."""
    _safe(deleted, errors, "auth_record",
          lambda: _delete_one(config.auth_table, {"phoneNumber": phone}))

    def _delete_tokens():
        total = 0
        if phone:
            items = _query_all(
                config.auth_tokens_table,
                IndexName="phone-index",
                KeyConditionExpression=Key("phoneNumber").eq(phone),
            )
            total += _batch_delete(
                config.auth_tokens_table, items,
                lambda i: {"token_id": i["token_id"], "purpose": i["purpose"]},
            )
        if email:
            items = _query_all(
                config.auth_tokens_table,
                IndexName="email-index",
                KeyConditionExpression=Key("email").eq(email.lower()),
            )
            total += _batch_delete(
                config.auth_tokens_table, items,
                lambda i: {"token_id": i["token_id"], "purpose": i["purpose"]},
            )
        return total

    _safe(deleted, errors, "auth_tokens", _delete_tokens)


# --------------------------------------------------------------------------- #
# User-scoped cleanup
# --------------------------------------------------------------------------- #
def _delete_user_scoped(user_id, deleted, errors):
    """Delete every row/object tied to a user_id."""
    # Users profile row
    _safe(deleted, errors, "users",
          lambda: _delete_one(config.users_table, {"user_id": user_id}))

    # Chat history (PK user_id, SK timestamp)
    _safe(deleted, errors, "chat_history", lambda: _batch_delete(
        config.chat_table,
        _query_all(config.chat_table,
                   KeyConditionExpression=Key("user_id").eq(user_id)),
        lambda i: {"user_id": user_id, "timestamp": i["timestamp"]},
    ))

    # Bookings (GSI_UserBookings -> base key PK/SK)
    _safe(deleted, errors, "bookings", lambda: _batch_delete(
        config.appointments_table,
        _query_all(config.appointments_table,
                   IndexName="GSI_UserBookings",
                   KeyConditionExpression=Key("user_id").eq(user_id)),
        lambda i: {"PK": i["PK"], "SK": i["SK"]},
    ))

    # Medilocker document metadata (PK user_id, SK created_at)
    _safe(deleted, errors, "medilocker_documents", lambda: _batch_delete(
        config.medilocker_documents_table,
        _query_all(config.medilocker_documents_table,
                   KeyConditionExpression=Key("user_id").eq(user_id)),
        lambda i: {"user_id": user_id, "created_at": i["created_at"]},
    ))

    # Subscriptions (GSI_UserSubscriptions -> base key subscription_id)
    _safe(deleted, errors, "subscriptions", lambda: _batch_delete(
        config.user_doctor_subscriptions_table,
        _query_all(config.user_doctor_subscriptions_table,
                   IndexName="GSI_UserSubscriptions",
                   KeyConditionExpression=Key("user_id").eq(user_id)),
        lambda i: {"subscription_id": i["subscription_id"]},
    ))

    # User<->Doctor unified relations (UserDoctor base key user_id + doctor_id)
    _safe(deleted, errors, "user_doctor", lambda: _batch_delete(
        config.user_doctor_table,
        _query_all(config.user_doctor_table,
                   KeyConditionExpression=Key("user_id").eq(user_id)),
        lambda i: {"user_id": i["user_id"], "doctor_id": i["doctor_id"]},
    ))

    # Legacy UserDoctorRelations rows (GSI_UserRelations -> base key relation_id)
    _safe(deleted, errors, "legacy_relations", lambda: _batch_delete(
        config.user_doctor_relations_table,
        _query_all(config.user_doctor_relations_table,
                   IndexName="GSI_UserRelations",
                   KeyConditionExpression=Key("user_id").eq(user_id)),
        lambda i: {"relation_id": i["relation_id"]},
    ))

    # Payments (no user GSI -> scan with filter, delete by payment_id)
    _safe(deleted, errors, "payments", lambda: _batch_delete(
        config.payments_table,
        _scan_all(config.payments_table,
                  FilterExpression=Attr("user_id").eq(user_id)),
        lambda i: {"payment_id": i["payment_id"]},
    ))

    # ABHA accounts linked to this Kokoro user, plus their ABDM transaction logs
    _safe(deleted, errors, "abha_accounts",
          lambda: _delete_abha_for_user(user_id, deleted, errors))

    # S3 Medilocker files
    _safe(deleted, errors, "s3_medilocker",
          lambda: _delete_s3_prefix(f"Medilocker/Users/{user_id}/"))


def _delete_abha_for_user(user_id, deleted, errors):
    """Delete AbhaAccounts rows for this user and best-effort their ABDM txns."""
    abha_rows = _query_all(
        config.abha_accounts_table,
        IndexName="kokoro_user_id-index",
        KeyConditionExpression=Key("kokoro_user_id").eq(user_id),
    )

    # Clean ABDM transaction logs keyed by the ABHA address (best effort)
    def _delete_abdm_txns():
        total = 0
        for row in abha_rows:
            abha_address = row.get("abha_address")
            if not abha_address:
                continue
            items = _query_all(
                config.abdm_transactions_table,
                IndexName="abha_address-index",
                KeyConditionExpression=Key("abha_address").eq(abha_address),
            )
            total += _batch_delete(
                config.abdm_transactions_table, items,
                lambda i: {"request_id": i["request_id"]},
            )
        return total

    _safe(deleted, errors, "abdm_transactions", _delete_abdm_txns)

    return _batch_delete(
        config.abha_accounts_table, abha_rows,
        lambda i: {"abha_number": i["abha_number"]},
    )


# --------------------------------------------------------------------------- #
# Doctor-scoped cleanup
# --------------------------------------------------------------------------- #
def _delete_doctor_scoped(doctor_id, deleted, errors):
    """Delete every row/object tied to a doctor_id."""
    # Doctors profile row
    _safe(deleted, errors, "doctors",
          lambda: _delete_one(config.doctors_table, {"doctor_id": doctor_id}))

    # Availability slots (PK doctor_id)
    _safe(deleted, errors, "availability", lambda: _batch_delete(
        config.availability_table,
        _query_all(config.availability_table,
                   KeyConditionExpression=Key("PK").eq(doctor_id)),
        lambda i: {"PK": doctor_id, "SK": i["SK"]},
    ))

    # Bookings (PK doctor_id)
    _safe(deleted, errors, "bookings", lambda: _batch_delete(
        config.appointments_table,
        _query_all(config.appointments_table,
                   KeyConditionExpression=Key("PK").eq(doctor_id)),
        lambda i: {"PK": i["PK"], "SK": i["SK"]},
    ))

    # Subscriptions where this doctor is the subscribed-to provider
    _safe(deleted, errors, "subscriptions", lambda: _batch_delete(
        config.user_doctor_subscriptions_table,
        _query_all(config.user_doctor_subscriptions_table,
                   IndexName="GSI_DoctorSubscribers",
                   KeyConditionExpression=Key("doctor_id").eq(doctor_id)),
        lambda i: {"subscription_id": i["subscription_id"]},
    ))

    # User<->Doctor unified relations (GSI_DoctorUsers -> base key user_id + doctor_id)
    _safe(deleted, errors, "user_doctor", lambda: _batch_delete(
        config.user_doctor_table,
        _query_all(config.user_doctor_table,
                   IndexName="GSI_DoctorUsers",
                   KeyConditionExpression=Key("doctor_id").eq(doctor_id)),
        lambda i: {"user_id": i["user_id"], "doctor_id": i["doctor_id"]},
    ))

    # Legacy UserDoctorRelations rows (GSI_DoctorRelations -> base key relation_id)
    _safe(deleted, errors, "legacy_relations", lambda: _batch_delete(
        config.user_doctor_relations_table,
        _query_all(config.user_doctor_relations_table,
                   IndexName="GSI_DoctorRelations",
                   KeyConditionExpression=Key("doctor_id").eq(doctor_id)),
        lambda i: {"relation_id": i["relation_id"]},
    ))

    # Earnings ledger (PK doctor_id)
    _safe(deleted, errors, "earnings", lambda: _batch_delete(
        config.doctor_earnings_table,
        _query_all(config.doctor_earnings_table,
                   KeyConditionExpression=Key("PK").eq(doctor_id)),
        lambda i: {"PK": i["PK"], "SK": i["SK"]},
    ))

    # Payouts (PK doctor_id)
    _safe(deleted, errors, "payouts", lambda: _batch_delete(
        config.doctor_payouts_table,
        _query_all(config.doctor_payouts_table,
                   KeyConditionExpression=Key("PK").eq(doctor_id)),
        lambda i: {"PK": i["PK"], "SK": i["SK"]},
    ))

    # Payments (no doctor GSI -> scan with filter, delete by payment_id)
    _safe(deleted, errors, "payments", lambda: _batch_delete(
        config.payments_table,
        _scan_all(config.payments_table,
                  FilterExpression=Attr("doctor_id").eq(doctor_id)),
        lambda i: {"payment_id": i["payment_id"]},
    ))

    # S3 doctor documents
    _safe(deleted, errors, "s3_doctor_documents",
          lambda: _delete_s3_prefix(f"DoctorDocuments/doctors/{doctor_id}/"))


# --------------------------------------------------------------------------- #
# Public orchestrator
# --------------------------------------------------------------------------- #
def delete_account_by_phone(phone_number: str, email_hint: str = None) -> dict:
    """
    Resolve a user and/or doctor account from any surviving trace and delete all
    related data. Safe to call repeatedly; cleans up partially-deleted accounts.

    Returns a dict:
        {
          "account_found": bool,
          "account_types": ["user"|"doctor", ...],
          "phone_number": str,
          "email": str|None,
          "user_id": str|None,
          "doctor_id": str|None,
          "deleted": {<resource>: <count>, ...},
          "errors": [str, ...],
        }
    """
    normalized_phone = normalize_phone_number(phone_number)
    if not normalized_phone:
        raise ValueError("Invalid phone number format")

    # --- Resolve identity from every available source ---------------------- #
    user = get_user_by_phone_for_admin(normalized_phone)
    doctor = get_doctor_by_phone_for_admin(normalized_phone)
    auth = _get_auth_record(normalized_phone)

    user_id = (user or {}).get("user_id") or (auth or {}).get("user_id")
    doctor_id = (doctor or {}).get("doctor_id") or (auth or {}).get("doctor_id")
    email = (
        (user or {}).get("email")
        or (doctor or {}).get("email")
        or (auth or {}).get("email")
        or email_hint
    )

    # Last-ditch recovery: if the auth record lost its user_id link but a Users
    # row still exists under the recovered email, recover the id from there.
    if not user_id and email:
        recovered = get_user_by_email(email)
        if recovered:
            user_id = recovered.get("user_id")

    deleted = {}
    errors = []
    account_types = []

    if user_id:
        account_types.append("user")
        _delete_user_scoped(user_id, deleted, errors)
    if doctor_id:
        account_types.append("doctor")
        _delete_doctor_scoped(doctor_id, deleted, errors)

    # Always sweep phone/email-keyed identity records (catches orphans even when
    # no user_id/doctor_id could be recovered).
    _delete_identity_records(normalized_phone, email, deleted, errors)

    total_deleted = sum(v for v in deleted.values() if isinstance(v, int))
    account_found = bool(user or doctor or auth) or total_deleted > 0

    logger.info(
        f"[delete-account] phone={normalized_phone} types={account_types} "
        f"user_id={user_id} doctor_id={doctor_id} "
        f"total_deleted={total_deleted} errors={len(errors)}"
    )

    return {
        "account_found": account_found,
        "account_types": account_types,
        "phone_number": normalized_phone,
        "email": email,
        "user_id": user_id,
        "doctor_id": doctor_id,
        "deleted": deleted,
        "errors": errors,
    }
