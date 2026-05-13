"""
Stage hospital staff bulk Excel uploads to S3 for async processing (24h SLA messaging).
Synchronous row processing lives in staff_service for workers or manual runs.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Literal

import boto3
from botocore.exceptions import ClientError
from fastapi import HTTPException

from app.config import AWS_REGION, S3_BUCKET
from app.logger import get_logger

logger = get_logger(__name__)

ImportKind = Literal["patient", "doctor"]


def stage_excel_import(
    *,
    kind: ImportKind,
    file_bytes: bytes,
    original_filename: str,
    context: Dict[str, Any],
) -> dict:
    """
    Upload .xlsx bytes and a sidecar manifest.json to S3.

    context for kind=patient: doctor_id, hospital_id (strings)
    context for kind=doctor: hospital_id, hospital_name (strings)
    """
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    fn = (original_filename or "").lower()
    if not fn.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="File must be .xlsx")

    staging_id = (
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:12]}"
    )

    if kind == "patient":
        hid = context.get("hospital_id")
        did = context.get("doctor_id")
        if not hid or not did:
            raise HTTPException(status_code=500, detail="Invalid staging context for patient import")
        prefix = f"hospital_staff/patient_imports/{hid}/{did}/{staging_id}"
    else:
        hid = context.get("hospital_id")
        if not hid:
            raise HTTPException(status_code=500, detail="Invalid staging context for doctor import")
        prefix = f"hospital_staff/doctor_imports/{hid}/{staging_id}"

    s3_key = f"{prefix}.xlsx"
    manifest_key = f"{prefix}.manifest.json"
    uploaded_at = datetime.now(timezone.utc).isoformat()

    manifest: Dict[str, Any] = {
        "import_kind": kind,
        "original_filename": original_filename or "upload.xlsx",
        "uploaded_at": uploaded_at,
        "staging_id": staging_id,
        **context,
    }

    s3 = boto3.client("s3", region_name=AWS_REGION)
    try:
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=file_bytes,
            ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=manifest_key,
            Body=json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json; charset=utf-8",
        )
    except ClientError as e:
        logger.error(f"[staff_excel_import_storage] S3 put failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to store import file")

    logger.info(
        f"[staff_excel_import_storage] staged kind={kind} s3_key={s3_key!r} manifest_key={manifest_key!r}"
    )
    return {
        "s3_key": s3_key,
        "manifest_key": manifest_key,
        "staging_id": staging_id,
    }
