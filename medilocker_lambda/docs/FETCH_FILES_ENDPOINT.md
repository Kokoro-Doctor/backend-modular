# Medilocker Fetch Files Endpoint

How `GET /medilocker/users/{user_id}/files` works — data sources, flow, and response.

---

## Endpoint

```
GET /medilocker/users/{user_id}/files
GET /medilocker/users/{user_id}/files?category=LAB_REPORT
```

**Path parameter:** `user_id` (required)

**Query parameter:** `category` (optional) — Filter by document category. Case-insensitive. Values: `LAB_REPORT`, `SCAN_REPORT`, `HEALTH_INSURANCE`, `HOSPITAL_RECORD`, `OTHER`.

**Purpose:** List all files in a user's Medilocker. Returns metadata only — no file content. Optionally filter by document category.

---

## Data Sources

| Source | Role |
|--------|------|
| **DynamoDB** | Primary source. All document metadata (filename, file_id, s3 keys, metadata) comes from the `MedilockerDocuments` table. |
| **S3** | Validation only. A lightweight `HEAD` request is used to confirm each file still exists. If the object is missing (404), the DynamoDB record is treated as orphaned and removed. |

---

## How It Works

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Client: GET /medilocker/users/{user_id}/files                           │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  1. Router (medilocker_router.py)                                        │
│     → file_service.fetch_files(user_id)                                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  2. document_db_service.get_documents_for_user(user_id)                  │
│     → DynamoDB Query on partition key user_id                            │
│     → ScanIndexForward=False (newest first)                              │
│     → Paginates if result > 1MB                                          │
│     → Returns full document records (filename, file_id, s3_original_key,  │
│       file_metadata, ocr_status, structured_status, etc.)                │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  3. For each document:                                                   │
│     a) s3_client.head_object(Bucket, s3_original_key)                   │
│        → Verifies file exists in S3                                      │
│     b) If 404/NoSuchKey → delete DynamoDB record, skip (orphan cleanup)  │
│     c) Else → append to files_info: { filename, file_id, metadata }       │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  4. Return { "files": files_info } or { "message": "No files found",     │
│     "files": [] }                                                        │
└─────────────────────────────────────────────────────────────────────────┘
```

### Summary

- **Metadata:** From DynamoDB (no S3 `GET` for content).
- **S3 usage:** Only `head_object` per document to check existence.
- **Orphan handling:** Missing S3 objects cause the corresponding DynamoDB record to be deleted and the file to be omitted from the response.

---

## API Response

### Success (files found)

```json
{
  "files": [
    {
      "filename": "prescription.pdf",
      "file_id": "a1b2c3d4",
      "document_category": "HOSPITAL_RECORD",
      "metadata": {
        "type": "prescription",
        "date": "2025-01-15"
      }
    },
    {
      "filename": "lab_report.jpg",
      "file_id": "e5f6g7h8",
      "document_category": "LAB_REPORT",
      "metadata": {
        "type": "lab_report",
        "date": "2025-01-10"
      }
    }
  ]
}
```

### Success (no files)

```json
{
  "message": "No files found",
  "files": []
}
```

### Error (500)

```json
{
  "detail": "Error message"
}
```

---

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `filename` | string | Display name of the file (from DynamoDB) |
| `file_id` | string | 8-char ID used for download and delete |
| `document_category` | string | Extracted category: `LAB_REPORT`, `SCAN_REPORT`, `HEALTH_INSURANCE`, `HOSPITAL_RECORD`, or `OTHER`. Defaults to `OTHER` for older records. |
| `metadata` | object | Arbitrary metadata stored at upload (e.g. `type`, `date`) |

---

## What Is Not Returned

- File content (use the download endpoint for that)
- `ocr_status`, `structured_status`, `s3_original_key`, etc. — these stay internal
- DynamoDB keys (`user_id`, `created_at`)

---

## Related Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /medilocker/users/{user_id}/files/{file_id}/download` | Returns a presigned S3 URL to download the file |
| `DELETE /medilocker/users/{user_id}/files/{file_id}` | Deletes the file from S3 and DynamoDB |
