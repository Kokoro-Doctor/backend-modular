"""
Document DB service - handles DynamoDB operations for MedilockerDocuments table.

Architecture:
  This module manages document metadata in DynamoDB alongside S3 storage.
  Every uploaded file gets a DynamoDB record tracking its S3 keys, OCR status,
  and timestamps.  The prescription flow queries DynamoDB instead of listing S3
  objects, and reads pre-computed OCR text rather than re-running Textract.

Table Design:
  Table:  MedilockerDocuments (see template.yaml)
  PK: user_id (string)
  SK: created_at (ISO 8601 string)
  file_id: normal attribute (8-char trimmed UUID)
"""
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError
from fastapi import HTTPException

from app.config import documents_table, s3_client, S3_BUCKET
from app.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def create_document_record(document_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Insert a new document metadata record into DynamoDB.

    Args:
        document_data: Must contain at minimum:
            - user_id (str)
            - file_id (str)
            - filename (str)
            - s3_original_key (str)
          Optional:
            - doc_type (str)
            - s3_ocr_key (str)
            - ocr_status (str)  — defaults to "PENDING"
            - structured_status (str)
            - document_category (str) — e.g. PRESCRIPTION, LAB_REPORT
            - confidence (float)
            - file_metadata (dict) — arbitrary metadata (file_type, file_size, etc.)

    Returns:
        The item that was written.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    user_id = document_data["user_id"]
    file_id = document_data["file_id"]

    item = {
        "user_id": user_id,
        "created_at": now_iso,
        "file_id": file_id,
        "filename": document_data["filename"],
        "doc_type": document_data.get("doc_type"),
        "s3_original_key": document_data["s3_original_key"],
        "s3_ocr_key": document_data.get("s3_ocr_key"),
        "ocr_status": document_data.get("ocr_status", "PENDING"),
        "confidence": document_data.get("confidence"),
        "structured_status": document_data.get("structured_status", "PENDING"),
        "document_category": document_data.get("document_category"),
        "file_metadata": document_data.get("file_metadata"),
        # upload_mode: "LIVE" (default) or "ASYNC" for background OCR jobs
        "upload_mode": document_data.get("upload_mode", "LIVE"),
        "updated_at": now_iso,
    }

    # Remove None values — DynamoDB doesn't support None for non-key attributes
    item = {k: v for k, v in item.items() if v is not None}

    try:
        documents_table.put_item(Item=item)
        logger.info(
            f"[DOC_DB] Created record user_id={user_id} created_at={now_iso} file_id={file_id}"
        )
        return item
    except ClientError as e:
        logger.error(f"[DOC_DB] Failed to create record for file_id={file_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create document record: {str(e)}",
        )


# ---------------------------------------------------------------------------
# Update OCR status
# ---------------------------------------------------------------------------

def update_ocr_status(
    user_id: str,
    file_id: str,
    status: str,
    confidence: Optional[float] = None,
    s3_ocr_key: Optional[str] = None,
    created_at: Optional[str] = None,
) -> bool:
    """
    Update the ocr_status for an existing document record.

    Pass created_at when available (e.g. from create_document_record) to avoid
    a lookup and ensure consistency immediately after insert.

    Args:
        user_id:     Partition key
        file_id:     Logical file identifier
        status:      "PENDING" | "COMPLETED" | "FAILED"
        confidence:  Optional OCR confidence score
        s3_ocr_key:  Optional S3 key for the OCR text file
        created_at:  Sort key — pass from create_document_record to avoid lookup

    Returns:
        True if update succeeded, False if the record was not found.
    """
    if created_at:
        sort_key = created_at
    else:
        record = get_document_by_file_id(user_id, file_id)
        if not record:
            logger.warning(
                f"[DOC_DB] Cannot update OCR status — record not found for "
                f"user_id={user_id} file_id={file_id}"
            )
            return False
        sort_key = record["created_at"]

    now_iso = datetime.now(timezone.utc).isoformat()

    update_expr_parts = [
        "#ocr_status = :status",
        "#updated_at = :updated_at",
    ]
    expr_attr_names = {
        "#ocr_status": "ocr_status",
        "#updated_at": "updated_at",
    }
    expr_attr_values: Dict[str, Any] = {
        ":status": status,
        ":updated_at": now_iso,
    }

    if confidence is not None:
        update_expr_parts.append("#confidence = :confidence")
        expr_attr_names["#confidence"] = "confidence"
        expr_attr_values[":confidence"] = str(confidence)

    if s3_ocr_key is not None:
        update_expr_parts.append("#s3_ocr_key = :s3_ocr_key")
        expr_attr_names["#s3_ocr_key"] = "s3_ocr_key"
        expr_attr_values[":s3_ocr_key"] = s3_ocr_key

    update_expression = "SET " + ", ".join(update_expr_parts)

    try:
        documents_table.update_item(
            Key={"user_id": user_id, "created_at": sort_key},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expr_attr_names,
            ExpressionAttributeValues=expr_attr_values,
        )
        logger.info(
            f"[DOC_DB] Updated OCR status to {status} for "
            f"user_id={user_id} file_id={file_id}"
        )
        return True
    except ClientError as e:
        logger.error(
            f"[DOC_DB] Failed to update OCR status for file_id={file_id}: {e}"
        )
        return False


# ---------------------------------------------------------------------------
# Update structured extraction result
# ---------------------------------------------------------------------------

def update_structured_data(
    user_id: str,
    file_id: str,
    status: str,
    structured_data_json: Optional[str] = None,
    document_category: Optional[str] = None,
    created_at: Optional[str] = None,
) -> bool:
    """
    Update the structured extraction result for a document.

    structured_data is stored as a JSON *string* to avoid DynamoDB
    Decimal / None type-conversion issues.

    Args:
        user_id:              Partition key
        file_id:              Logical file identifier
        status:               "PENDING" | "COMPLETED" | "FAILED"
        structured_data_json: JSON string of extracted structured data
        document_category:    Top-level category (e.g. LAB_REPORT, SCAN_REPORT)
        created_at:           Sort key (avoids lookup when available)

    Returns:
        True if update succeeded, False if the record was not found.
    """
    if created_at:
        sort_key = created_at
    else:
        record = get_document_by_file_id(user_id, file_id)
        if not record:
            logger.warning(
                f"[DOC_DB] Cannot update structured data — record not found "
                f"for user_id={user_id} file_id={file_id}"
            )
            return False
        sort_key = record["created_at"]

    now_iso = datetime.now(timezone.utc).isoformat()

    update_expr_parts = [
        "#structured_status = :status",
        "#structured_extracted_at = :extracted_at",
        "#updated_at = :updated_at",
    ]
    expr_attr_names = {
        "#structured_status": "structured_status",
        "#structured_extracted_at": "structured_extracted_at",
        "#updated_at": "updated_at",
    }
    expr_attr_values: Dict[str, Any] = {
        ":status": status,
        ":extracted_at": now_iso,
        ":updated_at": now_iso,
    }

    if structured_data_json is not None:
        update_expr_parts.append("#structured_data = :data")
        expr_attr_names["#structured_data"] = "structured_data"
        expr_attr_values[":data"] = structured_data_json

    if document_category is not None:
        update_expr_parts.append("#document_category = :category")
        expr_attr_names["#document_category"] = "document_category"
        expr_attr_values[":category"] = document_category

    update_expression = "SET " + ", ".join(update_expr_parts)

    try:
        documents_table.update_item(
            Key={"user_id": user_id, "created_at": sort_key},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expr_attr_names,
            ExpressionAttributeValues=expr_attr_values,
        )
        logger.info(
            f"[DOC_DB] Updated structured_status to {status} for "
            f"user_id={user_id} file_id={file_id}"
        )
        return True
    except ClientError as e:
        logger.error(
            f"[DOC_DB] Failed to update structured data for file_id={file_id}: {e}"
        )
        return False


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def delete_document_record(user_id: str, file_id: str) -> bool:
    """
    Delete a document record from DynamoDB.

    Returns True if deleted, False if not found.
    """
    record = get_document_by_file_id(user_id, file_id)
    if not record:
        return False
    try:
        documents_table.delete_item(
            Key={"user_id": user_id, "created_at": record["created_at"]},
        )
        logger.info(f"[DOC_DB] Deleted record for user_id={user_id} file_id={file_id}")
        return True
    except ClientError as e:
        logger.error(f"[DOC_DB] Failed to delete record for file_id={file_id}: {e}")
        return False


# ---------------------------------------------------------------------------
# Query: all documents for user (for list API)
# ---------------------------------------------------------------------------

def get_documents_for_user(
    user_id: str,
    category: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Return all document records for a user, newest first.

    Uses pagination to fetch all items (DynamoDB query limit is 1MB per call).

    Args:
        user_id: Partition key
        category: Optional filter by document_category (case-insensitive).
                  If provided, only items with document_category == category are returned.
    """
    items: List[Dict[str, Any]] = []
    kwargs = {
        "KeyConditionExpression": Key("user_id").eq(user_id),
        "ScanIndexForward": False,
    }
    if category:
        category_upper = category.strip().upper()
        kwargs["FilterExpression"] = Attr("document_category").eq(category_upper)
        logger.info(f"[DOC_DB] Filtering by document_category={category_upper}")

    try:
        while True:
            response = documents_table.query(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        logger.info(f"[DOC_DB] Fetched {len(items)} document(s) for user_id={user_id}")
        return items
    except ClientError as e:
        logger.error(f"[DOC_DB] Failed to query documents for user_id={user_id}: {e}")
        return []


# ---------------------------------------------------------------------------
# Query: latest documents
# ---------------------------------------------------------------------------

def get_latest_documents(user_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Return the most recent *limit* document records for a user, ordered by
    created_at descending (newest first).

    Uses ScanIndexForward=False for latest-first ordering.

    Args:
        user_id: Partition key
        limit:   Maximum number of records to return

    Returns:
        List of document items (may be empty).
    """
    try:
        response = documents_table.query(
            KeyConditionExpression=Key("user_id").eq(user_id),
            ScanIndexForward=False,  # descending (newest first)
            Limit=limit,
        )
        items = response.get("Items", [])
        logger.info(
            f"[DOC_DB] Fetched {len(items)} latest document(s) for user_id={user_id}"
        )
        return items
    except ClientError as e:
        logger.error(
            f"[DOC_DB] Failed to query latest documents for user_id={user_id}: {e}"
        )
        return []


# ---------------------------------------------------------------------------
# Query: single document by file_id
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Fetch stored OCR texts for claim autofill (Pipeline 2)
# ---------------------------------------------------------------------------

_CLAIM_CATEGORIES = {"INSURANCE_POLICY", "HOSPITAL_BILL", "PRESCRIPTION"}

_CATEGORY_TO_OCR_KEY: Dict[str, str] = {
    "INSURANCE_POLICY": "insurance_policy",
    "HOSPITAL_BILL": "hospital_bill",
    "PRESCRIPTION": "prescription",
}


def fetch_stored_ocr_for_user(user_id: str) -> Dict[str, Any]:
    """
    Fetch pre-computed OCR texts for the 3 mandatory claim documents.

    Queries MedilockerDocuments for INSURANCE_POLICY, HOSPITAL_BILL, and
    PRESCRIPTION categories (latest of each per category), verifies that
    OCR is COMPLETED for all, then reads each ocr.txt from S3.

    Returns on success:
        {"ocr_texts": {"insurance_policy": "...", "hospital_bill": "...", "prescription": "..."}}

    Returns on failure (caller inspects "error" key):
        {"error": "missing_docs",    "missing":         [...]}
        {"error": "ocr_pending",     "pending":         [...]}
        {"error": "s3_read_failed",  "failed_category": str}
    """
    all_docs = get_documents_for_user(user_id)

    # Pick the latest record per claim category (get_documents_for_user returns newest-first)
    doc_map: Dict[str, Dict[str, Any]] = {}
    for doc in all_docs:
        cat = (doc.get("document_category") or "").upper()
        if cat in _CLAIM_CATEGORIES and cat not in doc_map:
            doc_map[cat] = doc

    # Check for missing categories
    missing = [c for c in _CLAIM_CATEGORIES if c not in doc_map]
    if missing:
        logger.warning(
            f"[DOC_DB] fetch_stored_ocr: missing categories {missing} "
            f"for user_id={user_id}"
        )
        return {"error": "missing_docs", "missing": missing}

    # Check OCR completion for all found docs
    pending = [
        cat for cat, doc in doc_map.items()
        if doc.get("ocr_status") != "COMPLETED"
    ]
    if pending:
        logger.warning(
            f"[DOC_DB] fetch_stored_ocr: OCR not ready for {pending} "
            f"user_id={user_id}"
        )
        return {"error": "ocr_pending", "pending": pending}

    # Read ocr.txt from S3 for each document
    ocr_texts: Dict[str, str] = {}
    for cat, doc in doc_map.items():
        s3_key = doc.get("s3_ocr_key")
        ocr_key = _CATEGORY_TO_OCR_KEY[cat]
        if not s3_key:
            logger.error(
                f"[DOC_DB] fetch_stored_ocr: s3_ocr_key missing for "
                f"category={cat} file_id={doc.get('file_id')} user_id={user_id}"
            )
            return {"error": "s3_read_failed", "failed_category": cat}
        try:
            response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
            text = response["Body"].read().decode("utf-8")
            ocr_texts[ocr_key] = text
            logger.info(
                f"[DOC_DB] fetch_stored_ocr: read {len(text)} chars for "
                f"category={cat} file_id={doc.get('file_id')}"
            )
        except Exception as e:
            logger.error(
                f"[DOC_DB] fetch_stored_ocr: S3 read failed for "
                f"category={cat} key={s3_key}: {e}"
            )
            return {"error": "s3_read_failed", "failed_category": cat}

    return {"ocr_texts": ocr_texts}


def get_document_by_file_id(
    user_id: str, file_id: str
) -> Optional[Dict[str, Any]]:
    """
    Look up a single document record by file_id using the file_id-index GSI.

    Falls back to a partition query + filter if the GSI query returns nothing
    (e.g. GSI propagation delay).

    Args:
        user_id: Used for authorization and as fallback query key
        file_id: Logical file identifier (GSI hash key)

    Returns:
        The matching item dict, or None if not found.
    """
    # Primary path: GSI lookup (single-item read, no partition scan)
    try:
        response = documents_table.query(
            IndexName="file_id-index",
            KeyConditionExpression=Key("file_id").eq(file_id),
            Limit=1,
        )
        items = response.get("Items", [])
        if items:
            doc = items[0]
            if doc.get("user_id") == user_id:
                return doc
            logger.warning(
                f"[DOC_DB] file_id={file_id} belongs to user_id={doc.get('user_id')}, "
                f"not {user_id}"
            )
            return None
    except ClientError as e:
        logger.warning(f"[DOC_DB] GSI query failed for file_id={file_id}, trying fallback: {e}")

    # Fallback: partition query with filter (handles GSI propagation delay)
    try:
        response = documents_table.query(
            KeyConditionExpression=Key("user_id").eq(user_id),
            FilterExpression="file_id = :fid",
            ExpressionAttributeValues={":fid": file_id},
        )
        items = response.get("Items", [])
        if items:
            return items[0]
        return None
    except ClientError as e:
        logger.error(
            f"[DOC_DB] Failed to get document file_id={file_id} "
            f"for user_id={user_id}: {e}"
        )
        return None
