"""
HIP Data Flow business logic — Milestone 2, Section 6 (Kokoro → ABDM / HIU).

Pairs with the inbound callbacks in routers/webhook_router.py:

  6.3.1  consent notify        (ABDM → us)   ── webhook stores artefact, then ↓
  6.3.2  on-notify ack         (us → ABDM)   ── acknowledge_consent_notify()
  6.3.3  HI request            (ABDM → us)   ── webhook, then orchestrates ↓
  6.3.4  on-request ack        (us → ABDM)   ── acknowledge_hi_request()
  6.3.5  data push             (us → HIU)    ── push_health_data()
  6.3.6  health-info notify    (us → ABDM)   ── notify_data_transfer()

The full request→push→notify sequence is driven by
handle_health_information_request(): it acknowledges immediately (6.3.4), then
builds + encrypts the FHIR bundles and pushes them (6.3.5), then tells the CM
the outcome (6.3.6).

Encryption lives in app/abdm/data_encryption.py, over the Fidelius-compatible
primitives in app/abdm/fidelius.py. The acknowledgement (6.3.4) is always sent
before encryption is attempted, so a crypto failure still leaves ABDM with a
valid ack and us reporting FAILED to the CM — we never push placeholder data.
"""
import re
import uuid
from typing import List, Optional

from app.abdm import hip_client, data_encryption
from app.services import consent_service, abdm_transactions_service
from app.logger import get_logger

logger = get_logger(__name__)

# ABDM gateway paths (hit on ABDM_GATEWAY_BASE_URL via hip_client)
_ON_NOTIFY_PATH   = "/api/hiecm/consent/v3/request/hip/on-notify"          # 6.3.2
_ON_REQUEST_PATH  = "/api/hiecm/data-flow/v3/health-information/hip/on-request"  # 6.3.4
_NOTIFY_PATH      = "/api/hiecm/data-flow/v3/health-information/notify"    # 6.3.6


# ---------------------------------------------------------------------------
# 6.3.2 — Acknowledge the consent notify
# ---------------------------------------------------------------------------

def acknowledge_consent_notify(
    consent_id: str,
    notify_request_id: str,
    hip_id: str,
) -> None:
    """
    Respond to the 6.3.1 consent callback. `notify_request_id` is the REQUEST-ID
    header from that callback and is echoed back as response.requestId so ABDM
    can correlate the acknowledgement.
    """
    payload = {
        "acknowledgement": {
            "status":    "OK",
            "consentId": consent_id,
        },
        "response": {
            "requestId": notify_request_id,
        },
    }
    logger.info(
        "[DataFlowService] Acknowledging consent notify consent_id=%s request_id=%s hip_id=%s",
        consent_id, notify_request_id, hip_id,
    )
    hip_client.post(_ON_NOTIFY_PATH, payload, hip_id=hip_id, request_id=str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# 6.3.4 — Acknowledge the health-information request
# ---------------------------------------------------------------------------

def acknowledge_hi_request(
    transaction_id: str,
    hi_request_id: str,
    hip_id: str,
    session_status: str = "ACKNOWLEDGED",
) -> None:
    """
    Acknowledge the 6.3.3 HI request. `hi_request_id` is the REQUEST-ID header
    of that callback, echoed back as response.requestId.
    """
    payload = {
        "hiRequest": {
            "transactionId": transaction_id,
            "sessionStatus": session_status,
        },
        "response": {
            "requestId": hi_request_id,
        },
    }
    logger.info(
        "[DataFlowService] Acknowledging HI request transaction_id=%s request_id=%s hip_id=%s",
        transaction_id, hi_request_id, hip_id,
    )
    hip_client.post(_ON_REQUEST_PATH, payload, hip_id=hip_id, request_id=str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# 6.3.5 — Push encrypted data to the HIU dataPushUrl
# ---------------------------------------------------------------------------

def push_health_data(
    data_push_url: str,
    transaction_id: str,
    entries: List[dict],
    sender_key_material: dict,
    hip_id: str,
    page_number: int = 1,
    page_count: int = 1,
) -> None:
    """
    POST the encrypted FHIR entries to the HIU's dataPushUrl (6.3.5).
    `entries` and `sender_key_material` come from data_encryption.
    """
    payload = {
        "pageNumber":    page_number,
        "pageCount":     page_count,
        "transactionId": transaction_id,
        "entries":       entries,
        "keyMaterial":   sender_key_material,
    }
    logger.info(
        "[DataFlowService] Pushing %d entrie(s) transaction_id=%s url=%s",
        len(entries), transaction_id, data_push_url,
    )
    hip_client.post_to_url(data_push_url, payload, hip_id=hip_id, request_id=str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# 6.3.6 — Notify the CM of the data transfer status
# ---------------------------------------------------------------------------

def notify_data_transfer(
    consent_id: str,
    transaction_id: str,
    hip_id: str,
    care_context_references: List[str],
    session_status: str = "TRANSFERRED",
    hi_status: str = "OK",
    description: str = "",
) -> None:
    """
    Tell the CM whether the data transfer succeeded (6.3.6).
    session_status is one of TRANSFERRED | FAILED; per-careContext hi_status is
    one of DELIVERED | ERRORED (HIP side).
    """
    # ABDM rejects descriptions containing structural characters (it returned
    # "Invalid description" when passed a raw JSON error). Keep it to a short,
    # plain-text summary — strip anything but word chars and basic punctuation.
    safe_description = re.sub(r"[^\w .,:\-]", " ", description or "")
    safe_description = re.sub(r"\s+", " ", safe_description).strip()[:100]

    status_responses = [
        {
            "careContextReference": ref,
            "hiStatus":             hi_status,
            "description":          safe_description,
        }
        for ref in care_context_references
    ]
    payload = {
        "notification": {
            "consentId":     consent_id,
            "transactionId": transaction_id,
            "doneAt":        hip_client._utc_timestamp(),
            "notifier": {
                "type": "HIP",
                "id":   hip_id,
            },
            "statusNotification": {
                "sessionStatus":   session_status,
                "hipId":           hip_id,
                "statusResponses": status_responses,
            },
        },
    }
    logger.info(
        "[DataFlowService] Notifying CM transfer status=%s consent_id=%s transaction_id=%s",
        session_status, consent_id, transaction_id,
    )
    hip_client.post(_NOTIFY_PATH, payload, hip_id=hip_id, request_id=str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# FHIR bundle builder — placeholder
# ---------------------------------------------------------------------------

def _build_fhir_bundle(care_context_reference: str, consent_detail: dict) -> str:
    """
    Build the FHIR bundle JSON for a single care context.

    TODO: replace with real record assembly — pull the actual clinical data for
    `care_context_reference` and serialise it as an ABDM-compliant FHIR R4
    bundle (matching the hiTypes the consent granted). For now this returns a
    minimal placeholder so the surrounding flow is exercised end-to-end.
    """
    import json
    placeholder = {
        "resourceType": "Bundle",
        "type": "document",
        "meta": {"careContextReference": care_context_reference},
        "entry": [],
    }
    return json.dumps(placeholder)


# ---------------------------------------------------------------------------
# Orchestrator — 6.3.3 → 6.3.4 → 6.3.5 → 6.3.6
# ---------------------------------------------------------------------------

def handle_health_information_request(
    consent_id: str,
    transaction_id: str,
    hi_request_id: str,
    data_push_url: str,
    hiu_key_material: dict,
    hip_id: str,
) -> None:
    """
    Drive the full data flow after a 6.3.3 HI request lands:

      1. Acknowledge the request (6.3.4) — always, first.
      2. Build FHIR bundles for the consented care contexts and encrypt them.
      3. Push the encrypted entries to the HIU (6.3.5).
      4. Notify the CM that the transfer succeeded (6.3.6).

    A transaction row keyed by transaction_id tracks the overall flow. If the
    encryption module is still a stub (NotImplementedError) we stop after the
    acknowledgement and leave the row PENDING — no data is pushed. Any other
    failure notifies the CM with sessionStatus=FAILED and marks the row FAILED.
    """
    abdm_transactions_service.create_pending(
        request_id=transaction_id,
        api="hi-data-flow",
        hospital_id=hip_id,        # we only have hip_id here; keep both columns aligned
        hip_id=hip_id,
        abha_address=None,
        request_payload={
            "consent_id": consent_id,
            "data_push_url": data_push_url,
            "hi_request_id": hi_request_id,
        },
    )

    # 1) Acknowledge — must happen regardless of whether we can push yet.
    acknowledge_hi_request(transaction_id, hi_request_id, hip_id)

    care_context_refs = consent_service.get_care_context_references(consent_id)
    if not care_context_refs:
        logger.warning(
            "[DataFlowService] No care contexts found for consent_id=%s — nothing to push",
            consent_id,
        )

    consent_record = consent_service.get_hydrated(consent_id) or {}
    consent_detail = consent_record.get("consent_detail") or {}

    try:
        # 2) Build + encrypt the FHIR bundles for each consented care context.
        bundles = [
            (ref, _build_fhir_bundle(ref, consent_detail))
            for ref in care_context_refs
        ]
        entries, sender_key_material = data_encryption.encrypt_care_context_bundles(
            bundles, hiu_key_material,
        )

        # 3) Push to the HIU.
        push_health_data(
            data_push_url=data_push_url,
            transaction_id=transaction_id,
            entries=entries,
            sender_key_material=sender_key_material,
            hip_id=hip_id,
        )

        # 4) Notify the CM of success.
        notify_data_transfer(
            consent_id=consent_id,
            transaction_id=transaction_id,
            hip_id=hip_id,
            care_context_references=care_context_refs,
            session_status="TRANSFERRED",
            hi_status="OK",
        )

        abdm_transactions_service.mark_completed(
            transaction_id,
            callback_payload={"care_contexts": care_context_refs, "session_status": "TRANSFERRED"},
        )
        logger.info(
            "[DataFlowService] Data flow COMPLETED consent_id=%s transaction_id=%s",
            consent_id, transaction_id,
        )

    except NotImplementedError as e:
        # Encryption module not wired up yet — acknowledgement already sent.
        # Leave the transaction PENDING and surface clearly; do NOT push fake data
        # and do NOT tell the CM the transfer failed for an unbuilt feature.
        logger.warning(
            "[DataFlowService] Data push PENDING encryption module for "
            "consent_id=%s transaction_id=%s: %s",
            consent_id, transaction_id, e,
        )

    except Exception as e:
        logger.exception(
            "[DataFlowService] Data flow FAILED consent_id=%s transaction_id=%s",
            consent_id, transaction_id,
        )
        # Best-effort: tell the CM the transfer failed so it isn't left hanging.
        try:
            notify_data_transfer(
                consent_id=consent_id,
                transaction_id=transaction_id,
                hip_id=hip_id,
                care_context_references=care_context_refs,
                session_status="FAILED",
                hi_status="ERRORED",
                description=str(e)[:200],
            )
        except Exception:
            logger.exception("[DataFlowService] FAILED notify also errored")
        abdm_transactions_service.mark_failed(
            transaction_id, error={"message": str(e)},
        )
