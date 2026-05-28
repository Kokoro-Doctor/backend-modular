"""
Patient document upload service.

Handles the 3 mandatory documents collected at patient admission:
  - INSURANCE_POLICY
  - HOSPITAL_BILL
  - PRESCRIPTION

For each document this service:
  1. Validates file type and size.
  2. Uploads the original to S3 under Medilocker/Users/{user_id}/{file_id}/original.{ext}
     (same prefix as medilocker uploads so the OCR worker needs no changes).
  3. Writes a MedilockerDocuments record with ocr_status=PENDING and the
     document_category already set (no structured extraction needed).
  4. Enqueues an async OCR job on the existing SQS queue so OCRWorkerLambda
     runs Textract in the background.
"""
import json
import os
import unicodedata
from datetime import datetime, timezone
from uuid import uuid4

from botocore.exceptions import ClientError
from fastapi import HTTPException, UploadFile

from app.config import (
    DOCUMENTS_TABLE,
    MEDILOCKER_S3_PREFIX,
    OCR_QUEUE_URL,
    S3_BUCKET,
    s3_client,
    sqs_client,
)
from app.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png", "heic", "heif", "webp"}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

INSURANCE_POLICY = "INSURANCE_POLICY"
HOSPITAL_BILL = "HOSPITAL_BILL"
PRESCRIPTION = "PRESCRIPTION"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def upload_single_doc(user_id: str, doc_type: str, upload_file: UploadFile) -> dict:
    """Upload a single document for a patient and enqueue OCR. Returns the doc record dict."""
    return await _process_single_doc(user_id, doc_type, upload_file)


async def upload_patient_docs(
    user_id: str,
    insurance_policy: UploadFile,
    hospital_bill: UploadFile,
    prescription: UploadFile,
) -> list[dict]:
    """
    Upload all 3 mandatory patient documents and enqueue OCR jobs.

    Returns a list of dicts (one per doc) with keys:
      doc_type, document_category (same uppercase value), file_id, s3_original_key
    """
    uploads = [
        (INSURANCE_POLICY, insurance_policy),
        (HOSPITAL_BILL, hospital_bill),
        (PRESCRIPTION, prescription),
    ]

    results = []
    for doc_type, upload_file in uploads:
        result = await _process_single_doc(user_id, doc_type, upload_file)
        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Per-document pipeline
# ---------------------------------------------------------------------------

async def _process_single_doc(
    user_id: str,
    doc_type: str,
    upload_file: UploadFile,
) -> dict:
    filename = upload_file.filename or f"{doc_type}.bin"
    ext = _get_extension(filename)

    # ── Validate extension ──────────────────────────────────────────────────
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{doc_type}': file type '.{ext}' is not allowed. "
                f"Accepted: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            ),
        )

    # ── Read bytes & validate size ──────────────────────────────────────────
    file_bytes = await upload_file.read()
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        size_mb = len(file_bytes) / (1024 * 1024)
        limit_mb = MAX_FILE_SIZE_BYTES / (1024 * 1024)
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{doc_type}': file too large ({size_mb:.1f} MB). "
                f"Maximum is {limit_mb:.0f} MB."
            ),
        )

    file_id = uuid4().hex[:8]
    original_key = f"{MEDILOCKER_S3_PREFIX}{user_id}/{file_id}/original.{ext}"
    ocr_key = f"{MEDILOCKER_S3_PREFIX}{user_id}/{file_id}/ocr.txt"

    # ── Upload original to S3 ───────────────────────────────────────────────
    safe_filename = _sanitize_filename(filename)
    try:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=original_key,
            Body=file_bytes,
            Metadata={"filename": safe_filename, "doc_type": doc_type},
        )
        logger.info(
            f"[PATIENT_DOCS] Uploaded {doc_type} file_id={file_id} "
            f"({len(file_bytes)} bytes) → s3://{S3_BUCKET}/{original_key}"
        )
    except ClientError as exc:
        logger.error(
            f"[PATIENT_DOCS] S3 upload failed for {doc_type} "
            f"file_id={file_id}: {exc}"
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to upload {doc_type} document",
        )

    # ── Write MedilockerDocuments record ────────────────────────────────────
    now_iso = datetime.now(timezone.utc).isoformat()
    item = {
        "user_id": user_id,
        "created_at": now_iso,
        "file_id": file_id,
        "filename": filename,
        "doc_type": doc_type,
        "document_category": doc_type,
        "s3_original_key": original_key,
        "s3_ocr_key": ocr_key,
        "ocr_status": "PENDING",
        "structured_status": "SKIPPED",
        "upload_mode": "ASYNC",
        "updated_at": now_iso,
    }

    try:
        DOCUMENTS_TABLE.put_item(Item=item)
        logger.info(
            f"[PATIENT_DOCS] DB record created doc_type={doc_type} "
            f"file_id={file_id} user_id={user_id}"
        )
    except ClientError as exc:
        logger.error(
            f"[PATIENT_DOCS] DynamoDB write failed for {doc_type} "
            f"file_id={file_id}: {exc}"
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save {doc_type} document record",
        )

    # ── Enqueue async OCR job ───────────────────────────────────────────────
    _enqueue_ocr(user_id, file_id, original_key, filename, now_iso, doc_type)

    return {
        "doc_type": doc_type,
        "document_category": doc_type,
        "file_id": file_id,
        "s3_original_key": original_key,
    }


# ---------------------------------------------------------------------------
# OCR queue helper
# ---------------------------------------------------------------------------

def _enqueue_ocr(
    user_id: str,
    file_id: str,
    s3_key: str,
    filename: str,
    created_at: str,
    doc_type: str,
) -> None:
    if not OCR_QUEUE_URL:
        logger.error("[PATIENT_DOCS] OCR_QUEUE_URL is not configured")
        raise HTTPException(
            status_code=500,
            detail="OCR queue not configured — set OCR_QUEUE_URL env var",
        )

    payload = {
        "user_id": user_id,
        "file_id": file_id,
        "s3_key": s3_key,
        "filename": filename,
        "created_at": created_at,
    }

    try:
        sqs_client.send_message(
            QueueUrl=OCR_QUEUE_URL,
            MessageBody=json.dumps(payload),
        )
        logger.info(
            f"[PATIENT_DOCS] OCR job enqueued doc_type={doc_type} "
            f"file_id={file_id} user_id={user_id}"
        )
    except ClientError as exc:
        logger.error(
            f"[PATIENT_DOCS] SQS SendMessage failed for {doc_type} "
            f"file_id={file_id}: {exc}"
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to enqueue OCR job for {doc_type}",
        )


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _get_extension(filename: str) -> str:
    _, ext = os.path.splitext(filename)
    return ext.lstrip(".").lower() or "bin"


def _sanitize_filename(filename: str) -> str:
    """Convert filename to ASCII for S3 metadata (S3 only accepts ASCII)."""
    normalized = unicodedata.normalize("NFKD", filename)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    return ascii_only.strip() or "unnamed"
