"""
OCR service — thin wrapper around ocr_core + async queue dispatch.

All direct Textract logic lives in the shared ocr_core Lambda Layer.
This module:
  1. Wraps ocr_core functions with FastAPI-compatible error handling so
     callers (file_service, extraction_service, insurance_extraction_service,
     discharge_extraction_service, claim_validator_graph) are unchanged.
  2. Adds enqueue_ocr_async() for the background upload flow.
"""
import json
import time
import boto3
from fastapi import HTTPException
from botocore.exceptions import ClientError

from app.config import AWS_REGION, OCR_QUEUE_URL
from app.logger import get_logger

# ocr_core is provided as a Lambda Layer mounted at /opt/python/
from ocr_core.ocr_engine import (
    extract_from_image as _core_extract_image,
    extract_from_pdf_s3 as _core_extract_pdf,
)

logger = get_logger(__name__)

# SQS client — module-level for reuse across warm invocations
_sqs_client = boto3.client("sqs", region_name=AWS_REGION)


# ---------------------------------------------------------------------------
# Public OCR functions (signatures unchanged — all existing callers work)
# ---------------------------------------------------------------------------

def extract_text_from_image(image_bytes: bytes) -> str:
    """
    Extract text from an image using Textract DetectDocumentText.

    Delegates to ocr_core.extract_from_image and converts RuntimeError
    into FastAPI HTTPException so existing callers are unaffected.

    Args:
        image_bytes: Raw image bytes

    Returns:
        Extracted text as a plain string.
    """
    start = time.time()
    try:
        text = _core_extract_image(image_bytes)
        logger.info(
            f"[OCR] Image extraction done in {time.time()-start:.2f}s "
            f"({len(text)} chars)"
        )
        return text
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        elapsed = time.time() - start
        logger.error(
            f"[OCR] Textract ClientError after {elapsed:.2f}s: {error_code}: {exc}"
        )
        raise HTTPException(
            status_code=500,
            detail=f"Textract error: {error_code} - {exc}",
        )
    except Exception as exc:
        elapsed = time.time() - start
        logger.error(
            f"[OCR] Unexpected error after {elapsed:.2f}s: "
            f"{type(exc).__name__}: {exc}"
        )
        raise HTTPException(
            status_code=500,
            detail=f"OCR extraction failed: {exc}",
        )


def extract_text_from_pdf_s3(s3_bucket: str, s3_key: str) -> str:
    """
    Extract text from a PDF stored in S3 using Textract async text detection.

    Uses StartDocumentTextDetection + GetDocumentTextDetection (cheaper than
    StartDocumentAnalysis; FORMS/TABLES are not needed — only LINE blocks are
    consumed downstream).

    Blocks (polls) inside the caller's Lambda invocation, which is acceptable
    for the insurance/discharge analyze endpoints where the user is waiting.
    For user-uploaded PDFs prefer the async upload endpoint instead.

    Args:
        s3_bucket: S3 bucket containing the PDF.
        s3_key:    S3 object key of the PDF.

    Returns:
        Extracted text as a plain string.

    Raises:
        ClientError: If the Textract API call itself fails.
        RuntimeError: If the job fails or times out.
    """
    start = time.time()
    try:
        text = _core_extract_pdf(s3_bucket, s3_key)
        logger.info(
            f"[OCR] PDF extraction done in {time.time()-start:.2f}s "
            f"({len(text)} chars)"
        )
        return text
    except ClientError:
        elapsed = time.time() - start
        logger.error(f"[OCR] Textract ClientError for PDF after {elapsed:.2f}s")
        raise
    except RuntimeError:
        elapsed = time.time() - start
        logger.error(f"[OCR] PDF extraction RuntimeError after {elapsed:.2f}s")
        raise
    except Exception as exc:
        elapsed = time.time() - start
        logger.error(
            f"[OCR] Unexpected PDF error after {elapsed:.2f}s: "
            f"{type(exc).__name__}: {exc}"
        )
        raise


# ---------------------------------------------------------------------------
# Async queue dispatch
# ---------------------------------------------------------------------------

def enqueue_ocr_async(
    user_id: str,
    file_id: str,
    s3_key: str,
    filename: str,
    created_at: str,
) -> None:
    """
    Send an OCR job message to the SQS queue and return immediately.

    The OCRWorkerLambda consumes the message and runs the full OCR +
    structured extraction pipeline in the background.

    Args:
        user_id:    Document owner (DynamoDB partition key).
        file_id:    Logical file identifier.
        s3_key:     S3 key of the already-uploaded original file.
        filename:   Original filename (used for image vs PDF routing).
        created_at: ISO timestamp from the DynamoDB record (sort key).

    Raises:
        HTTPException(500): If SQS SendMessage fails.
    """
    if not OCR_QUEUE_URL:
        logger.error("[OCR] OCR_QUEUE_URL is not configured")
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
        _sqs_client.send_message(
            QueueUrl=OCR_QUEUE_URL,
            MessageBody=json.dumps(payload),
        )
        logger.info(
            f"[OCR] Enqueued async OCR job for file_id={file_id} "
            f"user_id={user_id}"
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        logger.error(
            f"[OCR] SQS SendMessage failed for file_id={file_id}: "
            f"{error_code}: {exc}"
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to enqueue OCR job: {error_code}",
        )
