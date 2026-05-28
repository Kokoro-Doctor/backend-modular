"""
OCR Worker Service — background OCR pipeline.

Called by handler.py for each SQS message. Responsibilities:
  1. Fetch original file bytes from S3.
  2. Route to image or PDF OCR via ocr_core (Lambda Layer).
  3. Write extracted text (ocr.txt) back to S3.
  4. Update DynamoDB: ocr_status = COMPLETED (or FAILED).

This module has NO dependency on medilocker_lambda — it is fully self-contained.
"""
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from botocore.exceptions import ClientError

from app.config import (
    S3_BUCKET,
    S3_FOLDER_PREFIX,
    s3_client,
    documents_table,
    IMAGE_EXTENSIONS,
    PDF_EXTENSIONS,
)
from app.logger import get_logger

# ocr_core is provided as a Lambda Layer mounted at /opt/python/
from ocr_core.ocr_engine import extract_from_image, extract_from_pdf_s3

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def process_ocr_job(
    user_id: str,
    file_id: str,
    s3_key: str,
    filename: str,
    created_at: str,
) -> None:
    """
    OCR pipeline for a single document: extract text, store ocr.txt, update DB.

    This is the top-level function called by handler.py per SQS record.
    All failures are caught internally — the function never raises so that
    SQS does not requeue a job that was processed (even if it failed). Only
    transient infrastructure errors (S3 unreachable, DynamoDB throttle) will
    propagate as exceptions and trigger SQS redelivery / DLQ routing.

    Args:
        user_id:    Document owner (DynamoDB PK).
        file_id:    Logical file identifier.
        s3_key:     S3 key of the original uploaded file.
        filename:   Original filename — used to detect image vs PDF.
        created_at: DynamoDB sort key from create_document_record.
    """
    start = time.time()
    logger.info(
        f"[WORKER] Starting job file_id={file_id} user_id={user_id} "
        f"filename={filename}"
    )

    # ── Step 1: OCR ────────────────────────────────────────────────────────
    ocr_text = _run_ocr(file_id, s3_key, filename)

    if not ocr_text:
        _update_ocr_status(user_id, file_id, created_at, "FAILED")
        logger.warning(
            f"[WORKER] OCR produced no text for file_id={file_id} — marked FAILED"
        )
        return

    # ── Step 2: Persist ocr.txt to S3 ──────────────────────────────────────
    ocr_key = f"{S3_FOLDER_PREFIX}{user_id}/{file_id}/ocr.txt"
    try:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=ocr_key,
            Body=ocr_text.encode("utf-8"),
            ContentType="text/plain; charset=utf-8",
        )
        logger.info(
            f"[WORKER] Stored ocr.txt ({len(ocr_text)} chars) at "
            f"s3://{S3_BUCKET}/{ocr_key}"
        )
    except ClientError as exc:
        logger.error(f"[WORKER] Failed to write ocr.txt for file_id={file_id}: {exc}")
        _update_ocr_status(user_id, file_id, created_at, "FAILED")
        return

    # ── Step 3: Mark OCR COMPLETED ─────────────────────────────────────────
    _update_ocr_status(
        user_id, file_id, created_at, "COMPLETED", s3_ocr_key=ocr_key
    )

    elapsed = time.time() - start
    logger.info(
        f"[WORKER] Job complete file_id={file_id} in {elapsed:.2f}s"
    )


# ---------------------------------------------------------------------------
# OCR helpers
# ---------------------------------------------------------------------------

def _run_ocr(file_id: str, s3_key: str, filename: str) -> Optional[str]:
    """
    Fetch the original file from S3 and run Textract OCR.

    Returns the extracted text string, or None on failure.
    """
    _, ext = os.path.splitext(filename)
    ext = ext.lstrip(".").lower() or "bin"

    # Fetch file bytes
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
        file_bytes = response["Body"].read()
        logger.info(
            f"[WORKER] Fetched {len(file_bytes)} bytes from "
            f"s3://{S3_BUCKET}/{s3_key}"
        )
    except ClientError as exc:
        logger.error(
            f"[WORKER] Cannot fetch s3://{S3_BUCKET}/{s3_key} "
            f"for file_id={file_id}: {exc}"
        )
        return None

    # Route to appropriate Textract path
    try:
        if ext in PDF_EXTENSIONS:
            logger.info(f"[WORKER] PDF path → StartDocumentTextDetection file_id={file_id}")
            text = extract_from_pdf_s3(S3_BUCKET, s3_key)
        elif ext in IMAGE_EXTENSIONS:
            logger.info(f"[WORKER] Image path → DetectDocumentText file_id={file_id}")
            text = extract_from_image(file_bytes)
        else:
            logger.error(
                f"[WORKER] Unsupported extension .{ext} for file_id={file_id}"
            )
            return None

        if not text or not text.strip():
            logger.warning(f"[WORKER] Textract returned empty text for file_id={file_id}")
            return None

        logger.info(f"[WORKER] OCR extracted {len(text)} chars for file_id={file_id}")
        return text

    except Exception as exc:
        logger.error(
            f"[WORKER] OCR failed for file_id={file_id} (.{ext}): "
            f"{type(exc).__name__}: {exc}"
        )
        return None


# ---------------------------------------------------------------------------
# DynamoDB helpers (self-contained — no medilocker_lambda imports)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_sort_key(user_id: str, file_id: str, created_at: Optional[str]) -> Optional[str]:
    """Resolve the DynamoDB sort key (created_at). Use passed value first."""
    if created_at:
        return created_at
    # Fallback: query by file_id GSI
    try:
        from boto3.dynamodb.conditions import Key
        resp = documents_table.query(
            IndexName="file_id-index",
            KeyConditionExpression=Key("file_id").eq(file_id),
            Limit=1,
        )
        items = resp.get("Items", [])
        if items and items[0].get("user_id") == user_id:
            return items[0]["created_at"]
    except Exception as exc:
        logger.warning(f"[WORKER] GSI lookup failed for file_id={file_id}: {exc}")
    return None


def _update_ocr_status(
    user_id: str,
    file_id: str,
    created_at: Optional[str],
    status: str,
    s3_ocr_key: Optional[str] = None,
) -> None:
    sort_key = _get_sort_key(user_id, file_id, created_at)
    if not sort_key:
        logger.error(
            f"[WORKER] Cannot update ocr_status — sort key not found "
            f"for file_id={file_id}"
        )
        return

    now = _now_iso()
    expr_parts = ["#ocr_status = :status", "#updated_at = :updated_at"]
    names = {"#ocr_status": "ocr_status", "#updated_at": "updated_at"}
    values: dict[str, Any] = {":status": status, ":updated_at": now}

    if s3_ocr_key:
        expr_parts.append("#s3_ocr_key = :s3_ocr_key")
        names["#s3_ocr_key"] = "s3_ocr_key"
        values[":s3_ocr_key"] = s3_ocr_key

    try:
        documents_table.update_item(
            Key={"user_id": user_id, "created_at": sort_key},
            UpdateExpression="SET " + ", ".join(expr_parts),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
        logger.info(
            f"[WORKER] ocr_status={status} for file_id={file_id}"
        )
    except ClientError as exc:
        logger.error(
            f"[WORKER] Failed to update ocr_status for file_id={file_id}: {exc}"
        )
