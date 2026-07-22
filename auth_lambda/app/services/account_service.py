"""
Account service - handles full account deletion business logic.

The public entry point is `delete_account_by_phone`. It resolves user, doctor,
and hospital identities from surviving traces, then sweeps the data owned by
those accounts. Shared people records are retained when a hospital is deleted.

Design notes:
  * It never bails just because the Users/Doctors profile row is gone. As long as
    an AuthTable record, an auth token, or any other row keyed to the recovered
    user_id/doctor_id exists, that data is deleted. This makes the endpoint
    self-correcting for accounts that were partially deleted by hand.
  * Every table operation is isolated: a failure on one table is logged and
    reported in `errors` but does not stop the rest of the cleanup.
  * `account_found` is True if any identity row existed OR anything was actually
    deleted, so the caller only returns 404 when the requested target is clean.
"""
from datetime import datetime, timezone

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


def _delete_s3_keys(keys):
    """Delete explicit S3 object keys, de-duplicated in batches of 1000."""
    unique_keys = sorted({key for key in keys if key})
    for i in range(0, len(unique_keys), 1000):
        config.s3_client.delete_objects(
            Bucket=config.S3_BUCKET,
            Delete={
                "Objects": [{"Key": key} for key in unique_keys[i:i + 1000]],
                "Quiet": True,
            },
        )
    return len(unique_keys)


def _safe(deleted, errors, label, fn):
    """Run a deletion step, recording its count or its error without raising."""
    try:
        count = fn()
        if isinstance(count, int) and isinstance(deleted.get(label), int):
            deleted[label] += count
        else:
            deleted[label] = count
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

    # Patient <-> hospital affiliations (GSI -> base composite key)
    _safe(deleted, errors, "user_hospital_memberships", lambda: _batch_delete(
        config.user_hospital_table,
        _query_all(config.user_hospital_table,
                   IndexName="GSI_UserHospitals",
                   KeyConditionExpression=Key("user_id").eq(user_id)),
        lambda i: {"hospital_id": i["hospital_id"], "user_id": i["user_id"]},
    ))

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

    # Doctor <-> hospital affiliations (GSI -> base composite key)
    _safe(deleted, errors, "doctor_hospital_memberships", lambda: _batch_delete(
        config.doctor_hospital_table,
        _query_all(config.doctor_hospital_table,
                   IndexName="GSI_DoctorHospitals",
                   KeyConditionExpression=Key("doctor_id").eq(doctor_id)),
        lambda i: {"hospital_id": i["hospital_id"], "doctor_id": i["doctor_id"]},
    ))

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
# Hospital-scoped cleanup
# --------------------------------------------------------------------------- #
def _get_hospital(hospital_id):
    try:
        return config.hospitals_table.get_item(
            Key={"hospital_id": hospital_id}
        ).get("Item")
    except Exception as e:
        logger.error(
            f"[delete-account] Error fetching hospital {hospital_id}: {e}",
            exc_info=True,
        )
        return None


def _find_hospitals_by_phone(raw_phone, normalized_phone):
    """Resolve hospital contacts despite legacy formatting differences."""
    candidates = []
    seen_ids = set()
    raw_phone = (raw_phone or "").strip()

    # Fast path for current exact GSI values.
    for contact in dict.fromkeys([raw_phone, normalized_phone]):
        if not contact:
            continue
        try:
            rows = _query_all(
                config.hospitals_table,
                IndexName="contact_number-index",
                KeyConditionExpression=Key("contact_number").eq(contact),
            )
        except Exception as e:
            logger.error(
                f"[delete-account] Hospital contact lookup failed for {contact}: {e}",
                exc_info=True,
            )
            rows = []
        for row in rows:
            hospital_id = row.get("hospital_id")
            if hospital_id and hospital_id not in seen_ids:
                seen_ids.add(hospital_id)
                candidates.append(row)

    if candidates:
        return candidates

    # Older hospital rows stored unnormalised contact values. The admin delete
    # path may scan as a fallback so those accounts remain deletable.
    try:
        for row in _scan_all(config.hospitals_table):
            contact = normalize_phone_number(row.get("contact_number") or "")
            hospital_id = row.get("hospital_id")
            if contact == normalized_phone and hospital_id not in seen_ids:
                seen_ids.add(hospital_id)
                candidates.append(row)
    except Exception as e:
        logger.error(
            f"[delete-account] Hospital contact fallback scan failed: {e}",
            exc_info=True,
        )
    return candidates


def _clear_hospital_profile_pointers(table, key_name, hospital_id):
    """Remove legacy, non-authoritative hospital pointers from profiles."""
    items = _scan_all(
        table,
        FilterExpression=Attr("hospital_id").eq(hospital_id),
    )
    count = 0
    for item in items:
        item_id = item.get(key_name)
        if not item_id:
            continue
        table.update_item(
            Key={key_name: item_id},
            UpdateExpression="REMOVE hospital_id, hospital_name",
        )
        count += 1
    return count


def _delete_hospital_user_doctor_relations(hospital_id):
    """Remove hospital assignments while retaining paid subscription bonds."""
    rows = _scan_all(
        config.user_doctor_table,
        FilterExpression=Attr("hospital_id").eq(hospital_id),
    )
    count = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    subscription_types = {"USER_SUBSCRIPTION", "SUBSCRIPTION"}
    for row in rows:
        key = {"user_id": row["user_id"], "doctor_id": row["doctor_id"]}
        has_subscription = bool(row.get("subscription_id")) or (
            row.get("relation_type") in subscription_types
        )
        if has_subscription:
            config.user_doctor_table.update_item(
                Key=key,
                UpdateExpression=(
                    "SET relation_type = :relation_type, linked_by = :linked_by, "
                    "updated_at = :updated_at REMOVE hospital_id"
                ),
                ExpressionAttributeValues={
                    ":relation_type": "USER_SUBSCRIPTION",
                    ":linked_by": "system",
                    ":updated_at": now_iso,
                },
            )
        else:
            config.user_doctor_table.delete_item(Key=key)
        count += 1
    return count


def _delete_hospital_documents(hospital_id, deleted, errors):
    """Delete hospital-uploaded document metadata and the referenced S3 data."""
    try:
        documents = _query_all(
            config.medilocker_documents_table,
            IndexName="hospital-index",
            KeyConditionExpression=Key("hospital_id").eq(hospital_id),
        )
    except Exception as e:
        logger.error(
            f"[delete-account] Error loading hospital_documents: {e}",
            exc_info=True,
        )
        errors.append(f"hospital_documents: {e}")
        return

    s3_keys = []
    for document in documents:
        s3_keys.extend([
            document.get("s3_original_key"),
            document.get("s3_ocr_key"),
        ])

    error_count = len(errors)
    _safe(deleted, errors, "s3_hospital_documents",
          lambda: _delete_s3_keys(s3_keys))
    if len(errors) == error_count:
        _safe(deleted, errors, "hospital_documents", lambda: _batch_delete(
            config.medilocker_documents_table,
            documents,
            lambda i: {"user_id": i["user_id"], "created_at": i["created_at"]},
        ))
    else:
        deleted.setdefault("hospital_documents", 0)


def _delete_legacy_hospital_files(hospital_id, deleted, errors):
    """Delete historical HospitalFiles rows and both legacy S3 namespaces."""
    try:
        rows = _query_all(
            config.hospital_files_table,
            KeyConditionExpression=Key("hospital_id").eq(hospital_id),
        )
    except Exception as e:
        logger.error(
            f"[delete-account] Error loading legacy_hospital_files: {e}",
            exc_info=True,
        )
        errors.append(f"legacy_hospital_files: {e}")
        rows = []

    error_count = len(errors)
    _safe(deleted, errors, "s3_legacy_hospital_files", lambda: _delete_s3_keys(
        [
            key
            for row in rows
            for key in (
                row.get("s3_key"),
                row.get("s3_original_key"),
                row.get("s3_ocr_key"),
            )
        ]
    ))
    _safe(deleted, errors, "s3_hospital_data_prefix",
          lambda: _delete_s3_prefix(f"HospitalData/{hospital_id}/"))
    _safe(deleted, errors, "s3_hospital_uploads_prefix",
          lambda: _delete_s3_prefix(f"hospital_uploads/{hospital_id}/"))
    if len(errors) == error_count:
        _safe(deleted, errors, "legacy_hospital_files", lambda: _batch_delete(
            config.hospital_files_table,
            rows,
            lambda i: {"hospital_id": i["hospital_id"], "file_id": i["file_id"]},
        ))
    else:
        deleted.setdefault("legacy_hospital_files", 0)


def _matches_hospital(item, hospital_id, service_ids):
    return (
        item.get("hospital_id") == hospital_id
        or item.get("hospital_id") in service_ids
        or item.get("hip_id") in service_ids
        or item.get("hiu_id") in service_ids
    )


def _delete_abha_link_tokens(service_ids):
    """Remove only this hospital's HIP-scoped link tokens from shared ABHA rows."""
    if not service_ids:
        return 0
    rows = _scan_all(config.abha_accounts_table)
    count = 0
    for row in rows:
        link_tokens = row.get("link_tokens") or {}
        token_ids = [service_id for service_id in service_ids if service_id in link_tokens]
        if not token_ids:
            continue
        remaining = set(link_tokens) - set(token_ids)
        if not remaining:
            expression = "REMOVE link_tokens"
            names = None
        else:
            names = {f"#service_{i}": service_id for i, service_id in enumerate(token_ids)}
            expression = "REMOVE " + ", ".join(
                f"link_tokens.#service_{i}" for i in range(len(token_ids))
            )
        kwargs = {
            "Key": {"abha_number": row["abha_number"]},
            "UpdateExpression": expression,
        }
        if names:
            kwargs["ExpressionAttributeNames"] = names
        config.abha_accounts_table.update_item(**kwargs)
        count += len(token_ids)
    return count


def _delete_hospital_abdm_data(hospital_id, deleted, errors):
    """Delete local ABDM configuration, requests, transactions and consents."""
    error_count = len(errors)
    try:
        abdm_config = config.hospital_abdm_table.get_item(
            Key={"hospital_id": hospital_id}
        ).get("Item") or {}
    except Exception as e:
        logger.error(
            f"[delete-account] Error loading hospital_abdm_config: {e}",
            exc_info=True,
        )
        errors.append(f"hospital_abdm_config: {e}")
        abdm_config = {}

    service_ids = {
        value
        for value in (abdm_config.get("hip_id"), abdm_config.get("hiu_id"))
        if value
    }

    def load_rows(label, table):
        try:
            return _scan_all(table)
        except Exception as e:
            logger.error(
                f"[delete-account] Error loading {label}: {e}",
                exc_info=True,
            )
            errors.append(f"{label}: {e}")
            return []

    all_transactions = load_rows("abdm_transactions", config.abdm_transactions_table)
    all_hiu_consents = load_rows("hiu_consent_requests", config.hiu_consent_requests_table)
    all_hiu_data = load_rows("hiu_data_requests", config.hiu_data_requests_table)

    # Rows with the Kokoro hospital ID can recover HIP/HIU identifiers even if
    # HospitalAbdmConfig was partially deleted before this retry.
    for row in all_transactions + all_hiu_consents + all_hiu_data:
        if row.get("hospital_id") == hospital_id:
            service_ids.update(
                value for value in (row.get("hip_id"), row.get("hiu_id")) if value
            )

    transactions = [
        row for row in all_transactions
        if _matches_hospital(row, hospital_id, service_ids)
    ]
    hiu_consents = [
        row for row in all_hiu_consents
        if _matches_hospital(row, hospital_id, service_ids)
    ]
    hiu_data = [
        row for row in all_hiu_data
        if _matches_hospital(row, hospital_id, service_ids)
    ]

    consent_ids = {
        row.get("consent_id")
        for row in hiu_data
        if row.get("consent_id")
    }
    for row in hiu_consents:
        consent_ids.update(row.get("consent_ids") or [])

    all_artefacts = load_rows("consent_artefacts", config.consent_artefacts_table)
    artefacts = [
        row for row in all_artefacts
        if row.get("consent_id") in consent_ids
        or row.get("hip_id") in service_ids
        or row.get("hiu_id") in service_ids
    ]

    _safe(deleted, errors, "abdm_transactions", lambda: _batch_delete(
        config.abdm_transactions_table,
        transactions,
        lambda i: {"request_id": i["request_id"]},
    ))
    _safe(deleted, errors, "hiu_consent_requests", lambda: _batch_delete(
        config.hiu_consent_requests_table,
        hiu_consents,
        lambda i: {"request_id": i["request_id"]},
    ))
    _safe(deleted, errors, "hiu_data_requests", lambda: _batch_delete(
        config.hiu_data_requests_table,
        hiu_data,
        lambda i: {"request_id": i["request_id"]},
    ))
    _safe(deleted, errors, "consent_artefacts", lambda: _batch_delete(
        config.consent_artefacts_table,
        artefacts,
        lambda i: {"consent_id": i["consent_id"]},
    ))
    _safe(deleted, errors, "abha_hospital_link_tokens",
          lambda: _delete_abha_link_tokens(service_ids))
    if len(errors) == error_count:
        _safe(deleted, errors, "hospital_abdm_config", lambda: _delete_one(
            config.hospital_abdm_table, {"hospital_id": hospital_id}
        ))
    else:
        deleted.setdefault("hospital_abdm_config", 0)


def _delete_hospital_scoped(hospital_id, deleted, errors):
    """Delete hospital-owned data without deleting shared people accounts."""
    error_count = len(errors)
    _safe(deleted, errors, "user_hospital_memberships", lambda: _batch_delete(
        config.user_hospital_table,
        _query_all(config.user_hospital_table,
                   KeyConditionExpression=Key("hospital_id").eq(hospital_id)),
        lambda i: {"hospital_id": i["hospital_id"], "user_id": i["user_id"]},
    ))
    _safe(deleted, errors, "doctor_hospital_memberships", lambda: _batch_delete(
        config.doctor_hospital_table,
        _query_all(config.doctor_hospital_table,
                   KeyConditionExpression=Key("hospital_id").eq(hospital_id)),
        lambda i: {"hospital_id": i["hospital_id"], "doctor_id": i["doctor_id"]},
    ))
    _safe(deleted, errors, "user_doctor_hospital_relations",
          lambda: _delete_hospital_user_doctor_relations(hospital_id))
    _safe(deleted, errors, "legacy_hospital_relations", lambda: _batch_delete(
        config.user_doctor_relations_table,
        _scan_all(config.user_doctor_relations_table,
                  FilterExpression=Attr("hospital_id").eq(hospital_id)),
        lambda i: {"relation_id": i["relation_id"]},
    ))
    _safe(deleted, errors, "user_hospital_pointers",
          lambda: _clear_hospital_profile_pointers(
              config.users_table, "user_id", hospital_id
          ))
    _safe(deleted, errors, "doctor_hospital_pointers",
          lambda: _clear_hospital_profile_pointers(
              config.doctors_table, "doctor_id", hospital_id
          ))
    _delete_hospital_documents(hospital_id, deleted, errors)
    _delete_legacy_hospital_files(hospital_id, deleted, errors)
    _delete_hospital_abdm_data(hospital_id, deleted, errors)

    # Delete the login/profile row last, and only after a clean sweep, so a
    # partial failure can still be resolved by contact number and retried.
    if len(errors) == error_count:
        _safe(deleted, errors, "hospitals", lambda: _delete_one(
            config.hospitals_table, {"hospital_id": hospital_id}
        ))
    else:
        deleted.setdefault("hospitals", 0)


# --------------------------------------------------------------------------- #
# Public orchestrator
# --------------------------------------------------------------------------- #
def delete_account_by_phone(
    phone_number: str = None,
    email_hint: str = None,
    hospital_id: str = None,
) -> dict:
    """
    Resolve user, doctor and hospital accounts from any surviving trace and
    delete related data. A hospital_id may be supplied for an exact hospital
    cleanup even if its Hospitals profile row is already gone.

    Returns a dict:
        {
          "account_found": bool,
          "account_types": ["user"|"doctor"|"hospital", ...],
          "phone_number": str,
          "email": str|None,
          "user_id": str|None,
          "doctor_id": str|None,
          "hospital_ids": [str, ...],
          "deleted": {<resource>: <count>, ...},
          "errors": [str, ...],
        }
    """
    requested_hospital_id = (hospital_id or "").strip() or None
    normalized_phone = None
    if phone_number:
        normalized_phone = normalize_phone_number(phone_number)
        if not normalized_phone:
            raise ValueError("Invalid phone number format")
    if not normalized_phone and not requested_hospital_id:
        raise ValueError("phoneNumber or hospital_id is required")

    # --- Resolve identity from every available source ---------------------- #
    user = get_user_by_phone_for_admin(normalized_phone) if normalized_phone else None
    doctor = get_doctor_by_phone_for_admin(normalized_phone) if normalized_phone else None
    auth = _get_auth_record(normalized_phone) if normalized_phone else None

    hospitals = []
    hospital_profile_found = False
    if requested_hospital_id:
        hospital = _get_hospital(requested_hospital_id)
        if hospital:
            hospital_profile_found = True
            hospitals.append(hospital)
        else:
            # Still sweep by exact ID to repair a partially-deleted hospital.
            hospitals.append({"hospital_id": requested_hospital_id})
    elif normalized_phone:
        hospitals = _find_hospitals_by_phone(phone_number, normalized_phone)
        hospital_profile_found = bool(hospitals)

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
    hospital_ids = []
    for hospital in hospitals:
        resolved_hospital_id = hospital.get("hospital_id")
        if not resolved_hospital_id or resolved_hospital_id in hospital_ids:
            continue
        hospital_ids.append(resolved_hospital_id)
        _delete_hospital_scoped(resolved_hospital_id, deleted, errors)

    if hospital_ids:
        account_types.append("hospital")

    # Always sweep phone/email-keyed identity records (catches orphans even when
    # no user_id/doctor_id could be recovered).
    if normalized_phone:
        _delete_identity_records(normalized_phone, email, deleted, errors)

    total_deleted = sum(v for v in deleted.values() if isinstance(v, int))
    account_found = (
        bool(user or doctor or auth)
        or hospital_profile_found
        or total_deleted > 0
    )

    logger.info(
        f"[delete-account] phone={normalized_phone} types={account_types} "
        f"user_id={user_id} doctor_id={doctor_id} hospital_ids={hospital_ids} "
        f"total_deleted={total_deleted} errors={len(errors)}"
    )

    return {
        "account_found": account_found,
        "account_types": account_types,
        "phone_number": normalized_phone,
        "email": email,
        "user_id": user_id,
        "doctor_id": doctor_id,
        "hospital_id": hospital_ids[0] if len(hospital_ids) == 1 else None,
        "hospital_ids": hospital_ids,
        "deleted": deleted,
        "errors": errors,
    }
