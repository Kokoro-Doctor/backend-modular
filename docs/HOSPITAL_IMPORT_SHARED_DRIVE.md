# `/hospital/import-shared-drive` Implementation

## Overview

The `POST /hospital/import-shared-drive` endpoint triggers a scan of a shared drive directory, ingests new medical files into S3 and DynamoDB, then moves processed files to a `processed/` subfolder to avoid duplicate ingestion. It is designed to be invoked on a schedule (e.g., via AWS EventBridge) or manually.

---

## Endpoint

| Method | Path | Auth |
|--------|------|------|
| `POST` | `/hospital/import-shared-drive` | `x-hospital-api-key` header (required) |

---

## Request

### Headers

| Header | Required | Description |
|--------|----------|-------------|
| `x-hospital-api-key` | Yes | API key for hospital authentication. Must match `HOSPITAL_API_KEY` env var. |

### Body (JSON)

```json
{
  "base_path": "/mnt/hospital_shared_drive"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `base_path` | string | Yes | Path to the shared drive root directory (e.g. `/mnt/hospital_shared_drive`). Must exist and be a directory. |

---

## Response

### Success (200 OK)

```json
{
  "message": "Shared drive import completed",
  "files_ingested": 5,
  "files_skipped": 2,
  "errors": []
}
```

| Field | Type | Description |
|-------|------|-------------|
| `message` | string | Success message |
| `files_ingested` | number | Count of files successfully uploaded to S3 and metadata saved to DynamoDB |
| `files_skipped` | number | Count of files skipped (e.g. disallowed type, oversized) |
| `errors` | array | List of error messages for failed files (e.g. read failed, move failed) |

### Error Responses

| Status | Condition | Details |
|--------|-----------|---------|
| `400` | Invalid `base_path` | Path does not exist or is not a directory |
| `401` | Missing/invalid API key | `x-hospital-api-key` header missing or invalid |
| `500` | Server error | Unexpected exception during scan |

---

## Expected Directory Structure

The shared drive must follow this structure:

```
base_path/
├── processed/           # Skipped during scan (contains already-processed files)
├── HOSP_001/
│   ├── PAT_001/
│   │   ├── file1.pdf
│   │   ├── file2.jpg
│   │   └── lab_report.pdf
│   └── PAT_002/
│       └── file3.pdf
└── HOSP_002/
    └── PAT_001/
        └── ...
```

- **Hospital folders** = `hospital_id` (e.g. `HOSP_001`)
- **Patient folders** = `patient_id` (e.g. `PAT_001`)
- **Files** = Direct children of patient folders (no nested subdirectories for files)
- **`processed/`** = Skipped at both `base_path` and `hospital_id` levels
- **Hidden files/dirs** (starting with `.`) = Skipped

---

## Processing Flow

1. **Validate** `base_path` exists and is a directory.
2. **Scan** all hospital folders under `base_path`.
3. For each hospital → patient → file:
   - Skip `processed/` folders and hidden entries.
   - Skip disallowed file types.
   - Skip files exceeding `MAX_FILE_SIZE_BYTES` (10 MB).
   - Read file → upload to S3 → save metadata to DynamoDB.
   - Move file to `processed/{hospital_id}/{patient_id}/` to avoid re-ingestion.

4. Return summary with `files_ingested`, `files_skipped`, and `errors`.

---

## File Handling

### Allowed Extensions

- **Images:** `jpg`, `jpeg`, `png`, `heic`, `heif`, `webp`, `tiff`, `tif`, `bmp`
- **Documents:** `pdf`, `doc`, `docx`, `xls`, `xlsx`, `csv`, `txt`

### Size Limit

- **Max:** 10 MB (`MAX_FILE_SIZE_BYTES` in `config.py`)

### S3 Storage

- **Prefix:** `HospitalData/`
- **Key pattern:** `HospitalData/{hospital_id}/{patient_id}/{file_id}/original.{ext}`
- **Metadata:** Original filename stored in S3 object metadata (ASCII-sanitized for non-ASCII)

### DynamoDB

- **Table:** `HospitalFiles` (or `HOSPITAL_FILES_TABLE` env var)
- **Metadata fields:** `hospital_id`, `patient_id`, `file_id`, `filename`, `file_type`, `s3_key`, `uploaded_at`, `upload_method` (`SHARED_DRIVE`), `file_size`

---

## Implementation Details

### Files

| File | Purpose |
|------|---------|
| `app/routers/hospital_router.py` | Endpoint handler |
| `app/services/hospital_service.py` | `scan_shared_drive()`, `ingest_shared_file()` |
| `app/models/schemas.py` | `HospitalImportSharedDriveRequest` |
| `app/config.py` | S3, DynamoDB, `HOSPITAL_API_KEY`, `HOSPITAL_DATA_PREFIX` |

### Key Functions

- **`scan_shared_drive(base_path)`** – Walks directory tree, validates structure, iterates hospital → patient → file, calls `ingest_shared_file()` for each.
- **`ingest_shared_file()`** – Validates, reads file, uploads to S3, saves metadata, moves to `processed/`.

### Duplicate Prevention

- Files are moved to `processed/{hospital_id}/{patient_id}/` after successful ingestion.
- If a file with the same name already exists in `processed/`, a numeric suffix is appended (e.g. `file_1.pdf`).

---

## Example Usage

### cURL

```bash
curl -X POST "https://your-api-url/hospital/import-shared-drive" \
  -H "Content-Type: application/json" \
  -H "x-hospital-api-key: YOUR_HOSPITAL_API_KEY" \
  -d '{"base_path": "/mnt/hospital_shared_drive"}'
```

### EventBridge Invocation

The endpoint can be invoked on a schedule via EventBridge HTTP target:

- **Method:** POST
- **URL:** `https://your-api-url/hospital/import-shared-drive`
- **Headers:** `Content-Type: application/json`, `x-hospital-api-key: <key>`
- **Body:** `{"base_path": "/mnt/hospital_shared_drive"}`

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `HOSPITAL_API_KEY` | Yes | — | API key for hospital endpoints |
| `HOSPITAL_FILES_TABLE` | No | `HospitalFiles` | DynamoDB table for file metadata |
| `S3_BUCKET` | No | `kokoro-doctor` | S3 bucket for uploads |

---

## Notes

- **No OCR/Textract/GPT:** This endpoint only stores raw files; no downstream processing.
- **Local filesystem:** The shared drive must be a mounted directory accessible to the Lambda/container (e.g. EFS mount).
- **Move failures:** If moving a file to `processed/` fails, the file is still ingested; an error is recorded and manual cleanup may be needed to avoid re-ingestion.
