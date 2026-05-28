"""
Hospital raw data ingestion service.

Handles raw file uploads from hospitals via API or presigned S3 URLs.
No OCR, Textract, or GPT processing — storage only.
"""
import os
import unicodedata
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException

from app.config import (
    s3_client,
    S3_BUCKET,
    HOSPITAL_DATA_PREFIX,
    HOSPITAL_UPLOADS_PREFIX,
    hospital_files_table,
    MAX_FILE_SIZE_BYTES,
)
from app.logger import get_logger

logger = get_logger(__name__)

# Common file extensions for hospital uploads (broader than Medilocker)
HOSPITAL_ALLOWED_EXTENSIONS = {
    "jpg", "jpeg", "png", "heic", "heif", "webp", "tiff", "tif", "bmp",
    "pdf", "doc", "docx", "xls", "xlsx", "csv", "txt",
}


def _get_extension(filename: str) -> str:
    """Extract and normalize file extension from filename."""
    _, ext = os.path.splitext(filename)
    ext = ext.lstrip(".").lower()
    return ext if ext else "bin"


def _sanitize_filename_for_s3_metadata(filename: str) -> str:
    """Convert filename to ASCII for S3 metadata (S3 only accepts ASCII)."""
    normalized = unicodedata.normalize("NFKD", filename)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    result = ascii_only.strip() or "unnamed"
    return result


def upload_file_api(
    hospital_id: str,
    patient_id: str,
    filename: str,
    file_bytes: bytes,
) -> dict:
    """
    Upload a file via direct API (binary content).

    1. Validate file type and size
    2. Generate file_id
    3. Upload to S3
    4. Save metadata in DynamoDB

    Returns:
        dict with file_id and message
    """
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        size_mb = len(file_bytes) / (1024 * 1024)
        limit_mb = MAX_FILE_SIZE_BYTES / (1024 * 1024)
        logger.warning(
            f"[HOSPITAL_ERROR] File too large: {size_mb:.1f} MB "
            f"(max {limit_mb:.0f} MB)"
        )
        raise HTTPException(
            status_code=400,
            detail=f"File too large ({size_mb:.1f} MB). Maximum is {limit_mb:.0f} MB.",
        )

    ext = _get_extension(filename)
    if ext not in HOSPITAL_ALLOWED_EXTENSIONS:
        logger.warning(
            f"[HOSPITAL_ERROR] Disallowed file type: .{ext}"
        )
        raise HTTPException(
            status_code=400,
            detail=f"File type '.{ext}' is not allowed. "
            f"Accepted: {', '.join(sorted(HOSPITAL_ALLOWED_EXTENSIONS))}",
        )

    file_id = uuid4().hex[:8]
    s3_key = f"{HOSPITAL_DATA_PREFIX}{hospital_id}/{patient_id}/{file_id}/original.{ext}"

    s3_client.put_object(
        Bucket=S3_BUCKET,
        Key=s3_key,
        Body=file_bytes,
        Metadata={"filename": _sanitize_filename_for_s3_metadata(filename)},
    )

    logger.info(
        f"[HOSPITAL_UPLOAD] Stored file at s3://{S3_BUCKET}/{s3_key} "
        f"(file_id={file_id}, size={len(file_bytes)} bytes)"
    )

    save_file_metadata(
        hospital_id=hospital_id,
        patient_id=patient_id,
        file_id=file_id,
        filename=filename,
        file_type=ext,
        s3_key=s3_key,
        upload_method="API_UPLOAD",
        file_size=len(file_bytes),
    )

    return {"file_id": file_id, "message": "File uploaded successfully"}


def generate_presigned_uploads_batch(
    hospital_id: str,
    patient_id: str,
    files: list[dict],
    expires_in: int = 3600,
) -> dict:
    """
    Generate presigned PUT URLs for multiple files.

    S3 key format: hospital_uploads/{hospital_id}/{patient_id}/{file_id}/{filename}

    Returns:
        dict with uploads: [{ file_id, filename, upload_url }, ...]
    """
    if not files:
        logger.warning("[HOSPITAL_PRESIGN] Empty files list")
        return {"uploads": []}

    uploads = []
    errors = []

    for item in files:
        filename = item.get("filename", "")
        if not filename:
            errors.append({"filename": filename, "detail": "Filename is required"})
            continue

        ext = _get_extension(filename)
        if ext not in HOSPITAL_ALLOWED_EXTENSIONS:
            logger.warning(f"[HOSPITAL_PRESIGN] Disallowed file type .{ext}: {filename}")
            errors.append(
                {
                    "filename": filename,
                    "detail": f"File type '.{ext}' is not allowed. "
                    f"Accepted: {', '.join(sorted(HOSPITAL_ALLOWED_EXTENSIONS))}",
                }
            )
            continue

        file_id = uuid4().hex[:8]
        safe_filename = os.path.basename(filename) or "unnamed"
        s3_key = (
            f"{HOSPITAL_UPLOADS_PREFIX}{hospital_id}/{patient_id}"
            f"/{file_id}/{safe_filename}"
        )

        try:
            upload_url = s3_client.generate_presigned_url(
                ClientMethod="put_object",
                Params={
                    "Bucket": S3_BUCKET,
                    "Key": s3_key,
                },
                ExpiresIn=expires_in,
            )
        except Exception as e:
            logger.exception(f"[HOSPITAL_PRESIGN] Failed to generate URL for {filename}: {e}")
            errors.append({"filename": filename, "detail": str(e)})
            continue

        logger.info(
            f"[HOSPITAL_PRESIGN] Generated presigned URL for file_id={file_id} "
            f"filename={filename} s3_key={s3_key}"
        )

        uploads.append({
            "file_id": file_id,
            "filename": filename,
            "upload_url": upload_url,
        })

    if errors and not uploads:
        raise HTTPException(
            status_code=400,
            detail=errors[0].get("detail", "Invalid request") if errors else "Invalid request",
        )

    return {"uploads": uploads}


def save_file_metadata(
    hospital_id: str,
    patient_id: str,
    file_id: str,
    filename: str,
    file_type: str,
    s3_key: str,
    upload_method: str,
    file_size: int | None = None,
) -> None:
    """
    Save file metadata to DynamoDB HospitalRawFiles table.

    Called after API upload (immediately) or after presigned upload
    (when hospital confirms upload completes).

    upload_method: "API_UPLOAD" or "PRESIGNED_UPLOAD"
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    item = {
        "hospital_id": hospital_id,
        "file_id": file_id,
        "patient_id": patient_id,
        "filename": filename,
        "file_type": file_type,
        "s3_key": s3_key,
        "uploaded_at": now_iso,
        "upload_method": upload_method,
        "file_size": file_size,
    }

    item = {k: v for k, v in item.items() if v is not None}

    hospital_files_table.put_item(Item=item)

    logger.info(
        f"[HOSPITAL_UPLOAD] Saved metadata for file_id={file_id} "
        f"hospital_id={hospital_id} patient_id={patient_id}"
    )


