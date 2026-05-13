"""
Hospital raw data ingestion router.

Endpoints for hospitals to upload raw medical files via API or presigned S3.
No OCR, Textract, or GPT — storage only.
"""
import os

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from botocore.exceptions import ClientError

from app.services import hospital_service
from app.models.schemas import (
    HospitalPresignRequest,
    HospitalConfirmUploadRequest,
)
from app.config import (
    HOSPITAL_API_KEY,
    HOSPITAL_UPLOADS_PREFIX,
    S3_BUCKET,
    s3_client,
)
from app.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/hospital", tags=["Hospital Raw Data"])


def require_api_key(x_hospital_api_key: str = Header(..., alias="x-hospital-api-key")) -> None:
    """Reject if x-hospital-api-key header is missing or invalid."""
    if not HOSPITAL_API_KEY:
        logger.error("[HOSPITAL_ERROR] HOSPITAL_API_KEY not configured")
        raise HTTPException(
            status_code=500,
            detail="Hospital API key not configured",
        )
    if x_hospital_api_key != HOSPITAL_API_KEY:
        logger.warning("[HOSPITAL_ERROR] Missing or invalid x-hospital-api-key")
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid x-hospital-api-key header",
        )


@router.post("/upload")
async def hospital_upload(
    hospital_id: str = Form(...),
    patient_id: str = Form(...),
    file: UploadFile = File(...),
    _: None = Depends(require_api_key),
):
    """
    Direct API upload: receive file as multipart/form-data, store in S3 and DynamoDB.

    Example request using curl:
        curl -X POST "API/hospital/upload" \\
          -H "x-hospital-api-key: KEY" \\
          -F "hospital_id=HOSP_001" \\
          -F "patient_id=PAT_001" \\
          -F "file=@lab_report.pdf"
    """
    try:
        contents = await file.read()
        filename = file.filename or "unnamed"
        file_size = len(contents)

        logger.info(
            f"[HOSPITAL_UPLOAD_MULTIPART] Received file filename={filename} "
            f"size={file_size} bytes"
        )

        result = hospital_service.upload_file_api(
            hospital_id=hospital_id,
            patient_id=patient_id,
            filename=filename,
            file_bytes=contents,
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[HOSPITAL_ERROR] API upload failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/presign-upload")
async def hospital_presign_upload(
    body: HospitalPresignRequest,
    _: None = Depends(require_api_key),
):
    """
    Generate presigned PUT URLs for direct S3 upload (supports multiple files).
    Hospital must call POST /hospital/confirm-upload after uploads complete.
    """
    try:
        if not body.files:
            raise HTTPException(
                status_code=400,
                detail="At least one file is required",
            )
        result = hospital_service.generate_presigned_uploads_batch(
            hospital_id=body.hospital_id,
            patient_id=body.patient_id,
            files=[{"filename": f.filename} for f in body.files],
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[HOSPITAL_ERROR] Presign failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/confirm-upload")
async def hospital_confirm_upload(
    body: HospitalConfirmUploadRequest,
    _: None = Depends(require_api_key),
):
    """
    Confirm presigned uploads completed. Saves metadata to DynamoDB for each file.
    Call after successfully uploading files to the presigned URLs.
    """
    try:
        confirmed = []
        errors = []

        for file_item in body.files:
            file_id = file_item.file_id
            filename = file_item.filename
            file_size = file_item.file_size

            ext = os.path.splitext(filename)[1].lstrip(".").lower() or "bin"
            safe_filename = os.path.basename(filename) or "unnamed"
            s3_key = (
                f"{HOSPITAL_UPLOADS_PREFIX}{body.hospital_id}/{body.patient_id}"
                f"/{file_id}/{safe_filename}"
            )

            try:
                s3_client.head_object(Bucket=S3_BUCKET, Key=s3_key)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code in ("404", "NoSuchKey"):
                    logger.warning(
                        f"[HOSPITAL_ERROR] File not found at s3://{S3_BUCKET}/{s3_key}"
                    )
                    errors.append({
                        "file_id": file_id,
                        "filename": filename,
                        "detail": "File not found in S3. Upload may not have completed.",
                    })
                    continue
                raise

            hospital_service.save_file_metadata(
                hospital_id=body.hospital_id,
                patient_id=body.patient_id,
                file_id=file_id,
                filename=filename,
                file_type=ext,
                s3_key=s3_key,
                upload_method="PRESIGNED_UPLOAD",
                file_size=file_size,
            )

            logger.info(
                f"[HOSPITAL_CONFIRM] Saved metadata for file_id={file_id} "
                f"hospital_id={body.hospital_id} patient_id={body.patient_id}"
            )

            confirmed.append({
                "file_id": file_id,
                "message": "Upload confirmed, metadata saved",
            })

        if errors and not confirmed:
            raise HTTPException(
                status_code=404,
                detail=errors[0].get("detail", "File not found in S3"),
            )

        return {
            "confirmed": confirmed,
            "errors": errors if errors else [],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[HOSPITAL_ERROR] Confirm upload failed")
        raise HTTPException(status_code=500, detail=str(e))


