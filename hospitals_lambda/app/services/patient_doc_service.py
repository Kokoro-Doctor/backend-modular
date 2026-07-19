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

from boto3.dynamodb.conditions import Attr, Key
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

async def upload_single_doc(
    user_id: str, doc_type: str, upload_file: UploadFile, hospital_id: str
) -> dict:
    """Upload a single document for a patient and enqueue OCR. Returns the doc record dict.

    hospital_id is the uploading hospital (from the JWT) — recorded on the doc
    so the hospital can later see its own uploads without exposing them to
    other hospitals.
    """
    return await _process_single_doc(user_id, doc_type, upload_file, hospital_id)


async def upload_patient_docs(
    user_id: str,
    insurance_policy: UploadFile,
    hospital_bill: UploadFile,
    prescription: UploadFile,
    hospital_id: str,
) -> list[dict]:
    """
    Upload all 3 mandatory patient documents and enqueue OCR jobs.

    hospital_id is the uploading hospital (from the JWT) — recorded on each doc.

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
        result = await _process_single_doc(user_id, doc_type, upload_file, hospital_id)
        results.append(result)

    return results


async def upload_documents_for_patient(
    user_id: str,
    hospital_id: str,
    files: list[UploadFile],
    metadata_by_filename: dict,
) -> list[dict]:
    """
    Attach one or more arbitrary documents to an existing patient, on behalf
    of a hospital — independent of the add-patient/update_patient forms.

    Unlike upload_patient_docs (fixed 3 admission documents), this accepts any
    number of files and a free-form doc_type per file (e.g. follow-up
    prescriptions, lab reports, scans added after admission). doc_type
    defaults to "OTHER" when not supplied. No check that user_id is an
    existing/linked patient — caller is responsible for passing a valid one.

    hospital_id is the uploading hospital (from the JWT) — recorded on each
    doc the same way as upload_patient_docs/upload_single_doc.
    """
    results = []
    for upload_file in files:
        file_meta = (metadata_by_filename or {}).get(upload_file.filename) or {}
        doc_type = (file_meta.get("doc_type") or "OTHER").strip().upper() or "OTHER"
        result = await _process_single_doc(user_id, doc_type, upload_file, hospital_id)
        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Per-document pipeline
# ---------------------------------------------------------------------------

async def _process_single_doc(
    user_id: str,
    doc_type: str,
    upload_file: UploadFile,
    hospital_id: str,
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
        # Uploaded by hospital staff on behalf of the patient.
        "source": "HOSPITAL",
        "hospital_id": hospital_id,
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


# ---------------------------------------------------------------------------
# Read — hospital-scoped view of a patient's documents
# ---------------------------------------------------------------------------

def list_patient_docs_for_hospital(user_id: str, hospital_id: str) -> list[dict]:
    """
    Documents a hospital may see for one patient: the patient's own uploads
    (source=USER) PLUS the docs *this* hospital uploaded — never another
    hospital's docs.

    Single user-partition query on MedilockerDocuments with a source/hospital_id
    filter. Returns newest-first, each with a short-lived presigned download URL.
    """
    items: list[dict] = []
    kwargs = {
        "KeyConditionExpression": Key("user_id").eq(user_id),
        "FilterExpression": Attr("source").eq("USER") | Attr("hospital_id").eq(hospital_id),
        "ScanIndexForward": False,
    }
    try:
        while True:
            response = DOCUMENTS_TABLE.query(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
    except ClientError as exc:
        logger.error(
            f"[PATIENT_DOCS] Hospital view query failed user_id={user_id} "
            f"hospital_id={hospital_id}: {exc}"
        )
        raise HTTPException(status_code=500, detail="Failed to fetch patient documents")

    docs = []
    for doc in items:
        s3_key = doc.get("s3_original_key")
        download_url = None
        if s3_key:
            try:
                download_url = s3_client.generate_presigned_url(
                    ClientMethod="get_object",
                    Params={"Bucket": S3_BUCKET, "Key": s3_key},
                    ExpiresIn=3600,
                )
            except ClientError as exc:
                logger.warning(
                    f"[PATIENT_DOCS] Presign failed for {s3_key}: {exc}"
                )
        docs.append({
            "file_id": doc.get("file_id"),
            "filename": doc.get("filename"),
            "doc_type": doc.get("doc_type"),
            "document_category": doc.get("document_category"),
            "source": doc.get("source", "USER"),
            "hospital_id": doc.get("hospital_id"),
            "ocr_status": doc.get("ocr_status"),
            "created_at": doc.get("created_at"),
            "download_url": download_url,
        })

    logger.info(
        f"[PATIENT_DOCS] Hospital view returned {len(docs)} doc(s) "
        f"user_id={user_id} hospital_id={hospital_id}"
    )
    return docs


def list_documents_for_hospital(hospital_id: str) -> list[dict]:
    """
    Every document this hospital uploaded, across all patients — the hospital
    dashboard view (as opposed to list_patient_docs_for_hospital, which is
    scoped to one patient and also includes that patient's own uploads).

    Uses the sparse hospital-index GSI (hospital_id -> created_at) on
    MedilockerDocuments, so only source=HOSPITAL rows for this hospital are
    ever returned — never another hospital's docs, and never patient
    self-uploads. Newest first.
    """
    items: list[dict] = []
    kwargs = {
        "IndexName": "hospital-index",
        "KeyConditionExpression": Key("hospital_id").eq(hospital_id),
        "ScanIndexForward": False,
    }
    try:
        while True:
            response = DOCUMENTS_TABLE.query(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
    except ClientError as exc:
        logger.error(
            f"[PATIENT_DOCS] Hospital-wide query failed hospital_id={hospital_id}: {exc}"
        )
        raise HTTPException(status_code=500, detail="Failed to fetch hospital documents")

    docs = []
    for doc in items:
        s3_key = doc.get("s3_original_key")
        download_url = None
        if s3_key:
            try:
                download_url = s3_client.generate_presigned_url(
                    ClientMethod="get_object",
                    Params={"Bucket": S3_BUCKET, "Key": s3_key},
                    ExpiresIn=3600,
                )
            except ClientError as exc:
                logger.warning(
                    f"[PATIENT_DOCS] Presign failed for {s3_key}: {exc}"
                )
        docs.append({
            "user_id": doc.get("user_id"),
            "file_id": doc.get("file_id"),
            "filename": doc.get("filename"),
            "doc_type": doc.get("doc_type"),
            "document_category": doc.get("document_category"),
            "ocr_status": doc.get("ocr_status"),
            "created_at": doc.get("created_at"),
            "download_url": download_url,
        })

    logger.info(
        f"[PATIENT_DOCS] Hospital-wide view returned {len(docs)} doc(s) "
        f"hospital_id={hospital_id}"
    )
    return docs
