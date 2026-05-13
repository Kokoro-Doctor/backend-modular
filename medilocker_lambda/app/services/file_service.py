"""
File service - handles S3 file operations for medilocker.

Architecture:
  DynamoDB is the source of truth for document metadata.

  Live upload (upload_files):
    Stores file in S3, creates DynamoDB record, then runs OCR + structured
    extraction synchronously before returning. Completes within the Lambda
    request lifecycle.

  Async upload (upload_files_async):
    Stores file in S3, creates DynamoDB record with ocr_status=PENDING, sends
    a message to the SQS OCR queue, and returns immediately with 202. The
    OCRWorkerLambda picks up the job and updates DynamoDB when done.
"""
import base64
import os
from typing import Optional
from uuid import uuid4
from fastapi import HTTPException
from botocore.exceptions import ClientError
from app.config import s3_client, S3_BUCKET, S3_FOLDER_PREFIX, ALLOWED_EXTENSIONS, MAX_FILE_SIZE_BYTES
from app.logger import get_logger
from app.services import document_db_service
from app.services import extraction_service
from app.services.ocr_service import extract_text_from_image, enqueue_ocr_async

PDF_EXTENSIONS = {"pdf"}
ASYNC_ALLOWED_EXTENSIONS = ALLOWED_EXTENSIONS | PDF_EXTENSIONS

logger = get_logger(__name__)


def upload_files(user_id: str, files):
    """
    Upload files for a user.

    1. Validate file type and size.
    2. Generate short file_id (8-char UUID).
    3. Store original at {prefix}{user_id}/{file_id}/original.{ext}
    4. Create DynamoDB record with ocr_status = PENDING.
    5. Run OCR + structured extraction synchronously (Lambda freezes after
       response, so background threads may not complete).
    """
    for file in files:
        # --- Validate file extension ---
        _, ext = os.path.splitext(file.filename)
        ext = ext.lstrip(".").lower()
        if not ext:
            ext = "bin"
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"File type '.{ext}' is not allowed. "
                       f"Accepted: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
            )

        try:
            file_binary = base64.b64decode(file.content)
        except base64.binascii.Error:
            raise HTTPException(status_code=400, detail="Invalid base64 encoding")

        # --- Validate decoded size ---
        if len(file_binary) > MAX_FILE_SIZE_BYTES:
            size_mb = len(file_binary) / (1024 * 1024)
            limit_mb = MAX_FILE_SIZE_BYTES / (1024 * 1024)
            raise HTTPException(
                status_code=400,
                detail=f"File too large ({size_mb:.1f} MB). Maximum is {limit_mb:.0f} MB.",
            )

        file_id = uuid4().hex[:8]
        original_key = f"{S3_FOLDER_PREFIX}{user_id}/{file_id}/original.{ext}"
        ocr_key = f"{S3_FOLDER_PREFIX}{user_id}/{file_id}/ocr.txt"

        # Upload original file to S3
        s3_metadata = {k: str(v) for k, v in (file.metadata or {}).items()}
        s3_metadata["filename"] = file.filename
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=original_key,
            Body=file_binary,
            Metadata=s3_metadata,
        )
        logger.info(f"[UPLOAD] Stored original at s3://{S3_BUCKET}/{original_key}")

        # Insert DynamoDB metadata record (ocr_status = PENDING)
        file_meta = {k: str(v) for k, v in (file.metadata or {}).items()}
        doc_type = file_meta.get("file_type") or None
        doc_record = document_db_service.create_document_record({
            "user_id": user_id,
            "file_id": file_id,
            "filename": file.filename,
            "doc_type": doc_type,
            "s3_original_key": original_key,
            "s3_ocr_key": ocr_key,
            "ocr_status": "PENDING",
            "file_metadata": file_meta or None,
        })
        created_at = doc_record["created_at"]

        # --- Run OCR + structured extraction synchronously ---
        # Lambda freezes when the handler returns; background threads may not
        # complete. Running inline ensures structured_status gets updated.
        _run_and_store_ocr(
            user_id=user_id,
            file_id=file_id,
            created_at=created_at,
            file_binary=file_binary,
            filename=file.filename,
            original_key=original_key,
            ocr_key=ocr_key,
        )
        logger.info(f"[UPLOAD] OCR + extraction completed for file_id={file_id}")


def upload_files_async(user_id: str, files):
    """
    Async upload — store files immediately, defer OCR to background worker.

    Flow per file:
      1. Validate file type (images + PDFs accepted) and decoded size.
      2. Store original at {prefix}{user_id}/{file_id}/original.{ext} in S3.
      3. Create DynamoDB record with ocr_status=PENDING, upload_mode=ASYNC.
      4. Send SQS message to OCRQueue → OCRWorkerLambda processes in background.

    Returns a list of {file_id, filename, status} dicts — one per file.
    The caller returns 202 to the client; poll GET /files/{file_id}/status.
    """
    results = []

    for file in files:
        _, ext = os.path.splitext(file.filename)
        ext = ext.lstrip(".").lower()
        if not ext:
            ext = "bin"

        if ext not in ASYNC_ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"File type '.{ext}' is not allowed. "
                    f"Accepted: {', '.join(sorted(ASYNC_ALLOWED_EXTENSIONS))}"
                ),
            )

        try:
            file_binary = base64.b64decode(file.content)
        except base64.binascii.Error:
            raise HTTPException(status_code=400, detail="Invalid base64 encoding")

        if len(file_binary) > MAX_FILE_SIZE_BYTES:
            size_mb = len(file_binary) / (1024 * 1024)
            limit_mb = MAX_FILE_SIZE_BYTES / (1024 * 1024)
            raise HTTPException(
                status_code=400,
                detail=f"File too large ({size_mb:.1f} MB). Maximum is {limit_mb:.0f} MB.",
            )

        file_id = uuid4().hex[:8]
        original_key = f"{S3_FOLDER_PREFIX}{user_id}/{file_id}/original.{ext}"
        ocr_key = f"{S3_FOLDER_PREFIX}{user_id}/{file_id}/ocr.txt"

        # Store original file in S3
        s3_metadata = {k: str(v) for k, v in (file.metadata or {}).items()}
        s3_metadata["filename"] = file.filename
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=original_key,
            Body=file_binary,
            Metadata=s3_metadata,
        )
        logger.info(
            f"[UPLOAD_ASYNC] Stored original at s3://{S3_BUCKET}/{original_key}"
        )

        # Create DynamoDB record — PENDING, upload_mode=ASYNC
        file_meta = {k: str(v) for k, v in (file.metadata or {}).items()}
        doc_type = file_meta.get("file_type") or None
        doc_record = document_db_service.create_document_record({
            "user_id": user_id,
            "file_id": file_id,
            "filename": file.filename,
            "doc_type": doc_type,
            "s3_original_key": original_key,
            "s3_ocr_key": ocr_key,
            "ocr_status": "PENDING",
            "upload_mode": "ASYNC",
            "file_metadata": file_meta or None,
        })
        created_at = doc_record["created_at"]

        # Dispatch to background worker via SQS
        enqueue_ocr_async(
            user_id=user_id,
            file_id=file_id,
            s3_key=original_key,
            filename=file.filename,
            created_at=created_at,
        )
        logger.info(
            f"[UPLOAD_ASYNC] Queued OCR job for file_id={file_id} "
            f"user_id={user_id}"
        )

        results.append({
            "file_id": file_id,
            "filename": file.filename,
            "status": "PENDING",
        })

    return results


def _run_and_store_ocr(
    user_id: str,
    file_id: str,
    created_at: str,
    file_binary: bytes,
    filename: str,
    original_key: str,
    ocr_key: str,
) -> None:
    """
    Run OCR on the uploaded file, persist the extracted text to S3, then
    run GPT structured extraction and persist the result in DynamoDB.

    Updates the DynamoDB record to COMPLETED or FAILED depending on outcome.
    This function never raises — errors are logged and the record is marked FAILED.
    """
    try:
        ocr_text = extract_text_from_image(file_binary)

        if not ocr_text or not ocr_text.strip():
            logger.warning(f"[UPLOAD_OCR] No text extracted from {filename} (file_id={file_id})")
            document_db_service.update_ocr_status(user_id, file_id, "FAILED", created_at=created_at)
            return

        # Store OCR text in S3
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=ocr_key,
            Body=ocr_text.encode("utf-8"),
            ContentType="text/plain; charset=utf-8",
        )
        logger.info(
            f"[UPLOAD_OCR] Stored OCR text ({len(ocr_text)} chars) at "
            f"s3://{S3_BUCKET}/{ocr_key}"
        )

        # Update DynamoDB → OCR COMPLETED
        document_db_service.update_ocr_status(
            user_id=user_id,
            file_id=file_id,
            status="COMPLETED",
            s3_ocr_key=ocr_key,
            created_at=created_at,
        )

        # --- Structured extraction (runs once at upload time) ---
        _run_and_store_structured_extraction(
            user_id=user_id,
            file_id=file_id,
            created_at=created_at,
            ocr_text=ocr_text,
            filename=filename,
        )

    except Exception as e:
        logger.error(
            f"[UPLOAD_OCR] OCR failed for file_id={file_id} ({filename}): "
            f"{type(e).__name__}: {str(e)}"
        )
        # Mark as FAILED so prescription flow knows not to use this document
        try:
            document_db_service.update_ocr_status(user_id, file_id, "FAILED", created_at=created_at)
        except Exception as update_err:
            logger.error(f"[UPLOAD_OCR] Also failed to mark FAILED: {update_err}")


def _run_and_store_structured_extraction(
    user_id: str,
    file_id: str,
    created_at: str,
    ocr_text: str,
    filename: str,
) -> None:
    """
    Run GPT structured extraction on OCR text and store the result in DynamoDB.

    Called once per document at upload time.  The structured JSON is stored as
    a string so the prescription endpoint can skip extraction entirely.

    This function never raises — errors are logged and structured_status is
    set to FAILED.
    """
    import json

    try:
        logger.info(
            f"[UPLOAD_EXTRACT] Running structured extraction for "
            f"file_id={file_id} ({filename})"
        )
        structured_data = extraction_service.extract_structured_data_from_text(ocr_text)

        document_category = structured_data.get("document_category", "OTHER")
        logger.info(
            f"[UPLOAD_EXTRACT] Category detected: {document_category} "
            f"for file_id={file_id}"
        )

        structured_json = json.dumps(structured_data)

        document_db_service.update_structured_data(
            user_id=user_id,
            file_id=file_id,
            status="COMPLETED",
            structured_data_json=structured_json,
            document_category=document_category,
            created_at=created_at,
        )
        logger.info(
            f"[UPLOAD_EXTRACT] Stored structured data ({len(structured_json)} chars) "
            f"for file_id={file_id}"
        )

    except Exception as e:
        logger.error(
            f"[UPLOAD_EXTRACT] Structured extraction failed for "
            f"file_id={file_id} ({filename}): {type(e).__name__}: {str(e)}"
        )
        try:
            document_db_service.update_structured_data(
                user_id=user_id,
                file_id=file_id,
                status="FAILED",
                created_at=created_at,
            )
        except Exception as update_err:
            logger.error(
                f"[UPLOAD_EXTRACT] Also failed to mark FAILED: {update_err}"
            )


def fetch_files(user_id: str, category: Optional[str] = None):
    """
    Fetch file metadata for a user. DynamoDB is the source of truth.

    Args:
        user_id: User ID
        category: Optional filter by document_category (e.g. LAB_REPORT). Case-insensitive.

    Returns filename (display), file_id (for download/delete), document_category, metadata.
    Metadata is read from DynamoDB (stored at upload time) — no S3 calls.
    Orphaned records (file missing from S3) are skipped and cleaned up.
    """
    db_docs = document_db_service.get_documents_for_user(user_id, category=category)
    files_info = []

    for doc in db_docs:
        s3_key = doc.get("s3_original_key")
        if not s3_key:
            continue

        # Verify the file still exists in S3 (lightweight HEAD)
        try:
            s3_client.head_object(Bucket=S3_BUCKET, Key=s3_key)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey"):
                logger.warning(
                    f"[FETCH] Orphaned record file_id={doc.get('file_id')} "
                    f"— S3 object missing, cleaning up"
                )
                document_db_service.delete_document_record(
                    user_id, doc.get("file_id")
                )
                continue
            # For other S3 errors, still include the file
            logger.warning(f"[FETCH] head_object error for {s3_key}: {e}")

        metadata = dict(doc.get("file_metadata") or {})
        doc_category = doc.get("document_category")
        if doc_category and "category" not in metadata:
            _cat_map = {
                "PRESCRIPTION": "prescription",
                "LAB_REPORT": "lab",
                "SCAN_REPORT": "scan",
                "HEALTH_INSURANCE": "insurance",
                "HOSPITAL_RECORD": "Medical",
            }
            metadata["category"] = _cat_map.get(doc_category.upper(), "prescription")
        files_info.append({
            "filename": doc.get("filename", ""),
            "file_id": doc.get("file_id"),
            "document_category": doc_category,
            "metadata": metadata,
        })

    return files_info


def _get_document_for_operation(user_id: str, file_id: str):
    """
    Fetch document by file_id. Uses s3_original_key from DynamoDB — no path reconstruction.
    """
    doc = document_db_service.get_document_by_file_id(user_id, file_id)
    if not doc:
        raise HTTPException(status_code=404, detail="File not found")
    return doc


def generate_download_link(user_id: str, file_id: str):
    """
    Generate presigned download URL. Uses s3_original_key from DynamoDB.
    """
    doc = _get_document_for_operation(user_id, file_id)
    s3_key = doc.get("s3_original_key")
    if not s3_key:
        raise HTTPException(status_code=404, detail="File not found")
    return s3_client.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": S3_BUCKET, "Key": s3_key},
        ExpiresIn=3600,
    )


def save_prescription_to_medilocker(
    user_id: str,
    prescription_pdf_base64: str,
    filename: Optional[str] = None,
) -> dict:
    """
    Save an approved prescription to Medilocker as a document.

    Stores the PDF in S3 and creates a DynamoDB record with
    document_category=PRESCRIPTION, ocr_status=COMPLETED, structured_status=COMPLETED.
    The prescription will appear in file listings and can be downloaded via the
    existing download endpoint.

    Args:
        user_id: Patient's user ID (owner of the Medilocker)
        prescription_pdf_base64: Base64-encoded PDF content
        filename: Optional display filename

    Returns:
        dict with file_id and filename for the saved prescription
    """
    from datetime import datetime, timezone
    import base64

    file_id = uuid4().hex[:8]
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")  # e.g. 2026-03-09
    time_str = now.strftime("%H%M")      # e.g. 1430
    time_full = now.strftime("%H:%M:%S") # e.g. 14:30:57

    content_bytes = base64.b64decode(prescription_pdf_base64)
    size_bytes = len(content_bytes)
    if size_bytes >= 1024 * 1024:
        file_size_str = f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        file_size_str = f"{size_bytes / 1024:.2f} KB"

    ext = "pdf"
    content_type = "application/pdf"
    display_filename = filename or f"Prescription_{date_str}_{time_str}.pdf"
    s3_key = f"{S3_FOLDER_PREFIX}{user_id}/{file_id}/prescription.pdf"

    s3_client.put_object(
        Bucket=S3_BUCKET,
        Key=s3_key,
        Body=content_bytes,
        ContentType=content_type,
        Metadata={"filename": display_filename},
    )
    logger.info(
        f"[SAVE_PRESCRIPTION] Stored prescription ({len(content_bytes)} bytes, {ext}) "
        f"at s3://{S3_BUCKET}/{s3_key}"
    )

    file_metadata = {
        "file_type": ext,
        "file_size": file_size_str,
        "upload_date": date_str,
        "upload_time": time_full,
        "category": "prescription",
        "source": "prescription_approval",
    }

    document_db_service.create_document_record({
        "user_id": user_id,
        "file_id": file_id,
        "filename": display_filename,
        "doc_type": ext,
        "s3_original_key": s3_key,
        "s3_ocr_key": s3_key,
        "ocr_status": "COMPLETED",
        "structured_status": "COMPLETED",
        "document_category": "PRESCRIPTION",
        "file_metadata": file_metadata,
    })

    logger.info(
        f"[SAVE_PRESCRIPTION] Created Medilocker document file_id={file_id} "
        f"for user_id={user_id}"
    )
    return {"file_id": file_id, "filename": display_filename}


def delete_file(user_id: str, file_id: str):
    """
    Delete a document and all associated S3 objects.

    Lists every object under the {user_id}/{file_id}/ prefix (original, OCR,
    any future artifacts) and batch-deletes them, then removes the DynamoDB record.
    """
    doc = _get_document_for_operation(user_id, file_id)

    # Delete all S3 objects under the file_id folder
    prefix = f"{S3_FOLDER_PREFIX}{user_id}/{file_id}/"
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        objects_to_delete = []
        for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                objects_to_delete.append({"Key": obj["Key"]})

        if objects_to_delete:
            # S3 batch delete supports up to 1000 keys per call
            for i in range(0, len(objects_to_delete), 1000):
                batch = objects_to_delete[i : i + 1000]
                s3_client.delete_objects(
                    Bucket=S3_BUCKET,
                    Delete={"Objects": batch, "Quiet": True},
                )
            logger.info(
                f"[DELETE] Removed {len(objects_to_delete)} S3 object(s) "
                f"under {prefix}"
            )
    except ClientError as e:
        logger.error(f"[DELETE] S3 batch delete failed for {prefix}: {e}")

    document_db_service.delete_document_record(user_id, file_id)

