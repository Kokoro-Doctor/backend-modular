# Medilocker — Complete System Documentation

## Overview

Medilocker is a medical document storage and processing system. Patients upload medical documents (prescriptions, lab reports, scans); the system extracts text via OCR, runs structured data extraction, and enables doctors to generate consolidated prescriptions.

**Stack:** FastAPI + Mangum (AWS Lambda), S3 (file storage), DynamoDB (metadata), Textract (OCR), GPT-4o (extraction + synthesis).

---

## Architecture

```
┌──────────────┐       ┌───────────────────────┐       ┌──────────────┐
│   Frontend   │──────▶│    API Gateway         │──────▶│  Lambda      │
│  (React      │       │    /medilocker/*       │       │  (FastAPI +  │
│   Native)    │◀──────│                        │◀──────│   Mangum)    │
└──────────────┘       └───────────────────────┘       └──────┬───────┘
                                                              │
                               ┌──────────────────────────────┼──────────────┐
                               │                              │              │
                          ┌────▼─────┐    ┌──────────────┐  ┌─▼────────┐   │
                          │    S3    │    │   DynamoDB    │  │ Textract │   │
                          │          │    │  Medilocker-  │  │  (OCR)   │   │
                          │ kokoro-  │    │  Documents    │  └──────────┘   │
                          │ doctor   │    └──────────────┘                  │
                          └──────────┘                        ┌────────┐   │
                                                              │ OpenAI │   │
                                                              │ GPT-4o │───┘
                                                              └────────┘
```

### Service Components

| Component | File | Role |
|-----------|------|------|
| App entry | `app/main.py` | FastAPI app, CORS, Mangum handler |
| Router | `app/routers/medilocker_router.py` | REST endpoints — thin wrapper |
| File service | `app/services/file_service.py` | Upload, list, download, delete (S3 + DynamoDB) |
| Document DB service | `app/services/document_db_service.py` | DynamoDB CRUD for document records |
| OCR service | `app/services/ocr_service.py` | AWS Textract integration |
| Prescription service | `app/services/prescription_service.py` | GPT extraction + synthesis |
| Config | `app/config.py` | S3/DynamoDB clients, env vars, constants |
| Schemas | `app/models/schemas.py` | Pydantic request models |

---

## Data Model

### S3 Storage

```
kokoro-doctor/
└── Medilocker/Users/
    └── {user_id}/
        └── {file_id}/
            ├── original.{ext}   ← uploaded file
            └── ocr.txt          ← extracted text (written by background OCR)
```

- `file_id` is an 8-character trimmed UUID, generated at upload time.
- Original filename is stored in S3 object metadata and in DynamoDB — never used in the S3 key path.

### DynamoDB — `MedilockerDocuments`

| Attribute | Type | Description |
|-----------|------|-------------|
| **`user_id`** (PK) | String | User identifier |
| **`created_at`** (SK) | String | ISO 8601 timestamp |
| `file_id` | String | 8-char UUID (has GSI: `file_id-index`) |
| `filename` | String | Original display filename |
| `doc_type` | String | File type (e.g. `pdf`, `jpg`) |
| `s3_original_key` | String | Full S3 key to uploaded file |
| `s3_ocr_key` | String | Full S3 key to `ocr.txt` |
| `ocr_status` | String | `PENDING` / `COMPLETED` / `FAILED` |
| `structured_data` | String | JSON string — GPT-extracted medical data |
| `structured_status` | String | `PENDING` / `COMPLETED` / `FAILED` |
| `document_category` | String | Extracted category: `LAB_REPORT`, `SCAN_REPORT`, `HEALTH_INSURANCE`, `HOSPITAL_RECORD`, `OTHER` |
| `structured_extracted_at` | String | ISO 8601 timestamp when extraction completed |
| `file_metadata` | Map | Client-provided metadata (file_type, file_size, dates) |
| `confidence` | String | Optional OCR confidence score |
| `updated_at` | String | Last update timestamp |

**GSI:** `file_id-index` — enables single-item lookup by `file_id` for download/delete operations.

DynamoDB is the **source of truth** for document metadata. S3 holds actual file bytes and OCR text.

---

## API Endpoints

### 1. Upload Files

```
POST /medilocker/upload
```

**Request:**
```json
{
  "user_id": "USR_abc123",
  "files": [
    {
      "filename": "prescription.pdf",
      "content": "<base64-encoded>",
      "metadata": {
        "file_type": "pdf",
        "file_size": "245.30 KB",
        "upload_date": "2/16/2026",
        "upload_time": "3:45:00 PM"
      }
    }
  ]
}
```

**Flow:**

```
Client
  │
  ▼
Validate extension (allow-list: pdf, jpg, jpeg, png, heic, heif, webp, tiff, tif, bmp)
  │
  ▼
Decode base64 → validate size (≤ 10 MB decoded)
  │
  ▼
Generate file_id (8-char UUID)
  │
  ▼
PUT to S3: Medilocker/Users/{user_id}/{file_id}/original.{ext}
  │
  ▼
Create DynamoDB record (ocr_status = PENDING, structured_status = PENDING)
  │
  ▼
Run OCR (Textract) — synchronously (inline)
  │
  ├── Success → PUT ocr.txt to S3, update ocr_status = COMPLETED
  │               │
  │               ▼
  │             Run GPT structured extraction on OCR text
  │               │
  │               ├── Success → store structured_data + document_category in DynamoDB
  │               └── Failure → structured_status = FAILED
  │
  └── Failure → ocr_status = FAILED
  │
  ▼
Return 200 ──────────────────────────────────────────  ← response sent here
```

OCR and structured extraction run **synchronously** (inline) before the response is sent. Lambda freezes when the handler returns, so background threads would not complete; running inline ensures `structured_status` and `document_category` are persisted before the Lambda execution context ends.

**Errors:**
- `400` — Invalid extension, file too large, bad base64
- `500` — S3/DynamoDB write failure

---

### 2. List Files

```
GET /medilocker/users/{user_id}/files
GET /medilocker/users/{user_id}/files?category=LAB_REPORT
```

**Query parameter:** `category` (optional) — Filter by document category. Case-insensitive. Values include: `LAB_REPORT`, `SCAN_REPORT`, `HEALTH_INSURANCE`, `INSURANCE_FORM`, `HOSPITAL_RECORD`, `OTHER`.

**Response:**
```json
{
  "files": [
    {
      "filename": "prescription.pdf",
      "file_id": "a1b2c3d4",
      "document_category": "HOSPITAL_RECORD",
      "metadata": {
        "file_type": "pdf",
        "file_size": "245.30 KB",
        "upload_date": "2/16/2026",
        "upload_time": "3:45:00 PM"
      }
    }
  ]
}
```

**Flow:**
1. Query DynamoDB for all documents for `user_id` (newest first). Optionally filter by `document_category`.
2. For each document, verify the file still exists in S3 (`head_object`).
   - If S3 returns 404 → auto-delete the orphaned DynamoDB record, skip the file.
3. Return `filename`, `file_id`, `document_category`, and `metadata` (from `file_metadata`) from DynamoDB (no S3 metadata reads needed).

**Frontend uses:** `file_id` for download, delete, and share operations.

---

### 3. Download File

```
GET /medilocker/users/{user_id}/files/{file_id}/download
```

**Response:**
```json
{
  "download_url": "https://s3.ap-south-1.amazonaws.com/kokoro-doctor/..."
}
```

**Flow:**
1. Look up document by `file_id` using the `file_id-index` GSI (with partition-scan fallback).
2. Verify `user_id` matches the document owner.
3. Read `s3_original_key` from the DynamoDB record.
4. Generate presigned URL (1-hour expiry).

**Errors:**
- `404` — Document not found or wrong user

---

### 4. Delete File

```
DELETE /medilocker/users/{user_id}/files/{file_id}
```

**Response:**
```json
{
  "message": "File deleted successfully"
}
```

**Flow:**
1. Look up document by `file_id`.
2. List all S3 objects under `Medilocker/Users/{user_id}/{file_id}/` prefix.
3. Batch-delete all objects (original, OCR text, any future artifacts).
4. Delete the DynamoDB record.

**Errors:**
- `404` — Document not found

---

### 5. Generate Prescription (from stored documents)

```
POST /medilocker/users/{user_id}/prescription
```

**Response:**
```json
{
  "prescription": "Diagnosis:\n...\n\nMedications:\n...",
  "patient_details": {
    "name": "John Doe",
    "age": 45,
    "gender": "Male",
    "diagnosis": "Type 2 Diabetes"
  }
}
```

**Flow:**

```
Query DynamoDB: latest N documents for user_id
  │
  ▼
Filter: ocr_status == COMPLETED AND structured_status == COMPLETED
  │
  ▼
Parse structured_data JSON from each document
  │
  ▼
Merge structured data across documents
  ├── De-duplicate medications (by name + dose)
  ├── Prefer latest patient details
  └── Combine diagnoses, symptoms, labs, etc.
  │
  ▼
GPT synthesis: single call → final prescription text
  │
  ▼
Return { prescription, patient_details }
```

**Key:** No OCR or per-document extraction happens at prescription time. All extraction is done once at upload time. The only GPT call here is the final synthesis.

- `N` = `PRESCRIPTION_MAX_DOCS` (env var, default `10`)
- Returns `{ "prescription": "" }` if no fully-processed documents exist.

---

### 6. Extract from Uploaded Files (direct, no storage)

```
POST /medilocker/prescription
```

**Request:**
```json
{
  "files": [
    { "filename": "scan.jpg", "content": "<base64>" }
  ]
}
```

Extracts structured medical data from uploaded files using GPT-4o Vision directly. Files are **not** stored in Medilocker — this is a stateless extraction endpoint used by the doctor's portal for on-the-fly analysis.

---

### 7. Insurance document extract (multipart, stored metadata)

```
POST /medilocker/users/{user_id}/insurance/analyze
```

**Request:** `multipart/form-data` with one part:

- `file` — insurance PDF or image (same image types as Medilocker upload allow-list, plus PDF)

**Response:**

```json
{
  "structured_data": { "...": "insurance-specific schema, document_category INSURANCE_FORM" },
  "analysis": {
    "is_complete": true,
    "missing_fields": [],
    "issues": [],
    "suggestions": [],
    "claim_opportunity": ""
  }
}
```

**Flow (see `insurance_extraction_service.py`):**

```
Upload bytes from multipart
  │
  ▼
PDF → temp S3 key → Textract StartDocumentAnalysis (async, multi-page) → LINE text
Image → Textract detect_document_text (bytes)
  │
  ▼
Groq: extract_insurance_structured_data_from_text
  │
  ▼
Groq: analyze_insurance_claim(structured_data)
  │
  ▼
DynamoDB: create_document_record + update_structured_data
  (structured_data field holds JSON: { structured_data, analysis })
  │
  ▼
Return { structured_data, analysis }
```

**Notes:**

- Independent from the medical `extraction_service` pipeline and prescription endpoints.
- Requires `GROQ_API_KEY` for LLM steps.
- Listing: `GET /medilocker/users/{user_id}/files?category=INSURANCE_FORM` (when filtering by stored `document_category`).

---

## Frontend Integration

### Service Layer — `frontend/utils/MedilockerService.js`

| Function | Endpoint | Notes |
|----------|----------|-------|
| `FetchFromServer(userId)` | `GET /users/{userId}/files` | Returns file list |
| `upload(payload)` | `POST /upload` | Sends base64 files |
| `download(userId, fileId)` | `GET /users/{userId}/files/{fileId}/download` | Returns presigned URL |
| `remove(userId, fileId)` | `DELETE /users/{userId}/files/{fileId}` | Deletes file + metadata |
| `extractStructuredData(files)` | `POST /prescription` | Direct extraction (no storage) |
| _(not wired in sample service)_ | `POST /users/{userId}/insurance/analyze` | Multipart `file` — insurance OCR + analysis + DB save |

### User Identifier

All screens use `user?.user_id || user?.email` as the user identifier. This is consistent across upload, list, download, and delete.

### Screens Using Medilocker

| Screen | File | Usage |
|--------|------|-------|
| **Medilocker** (patient) | `Medilocker.jsx` | Upload, list, download, delete, share |
| **UserDashboard** (patient) | `UserDashboard.jsx` | List, download, delete, share |
| **GeneratePrescription** (doctor) | `GeneratePrescription.jsx` | View patient files, download, delete, share, generate prescription |

### Upload UX (Mobile)

On mobile / small screens, the upload shows a progress bar that tracks the real network request:
- Ticks from 0→90% while `upload()` is in flight.
- Jumps to 100% on success.
- Shows error state if upload fails.
- "Success" only displays after the server confirms.

---

## Upload-Time Processing Pipeline

```
                         Upload Time (synchronous, inline)
                         ─────────────────────────────────
                         
  ┌─────────────────────────────────────────────────────────────────┐
  │                                                                 │
  │  OCR (Textract)                                                 │
  │  ├── Images → detect_document_text                              │
  │  └── PDFs   → analyze_document (via S3 object reference)        │
  │                                                                 │
  │         │                                                       │
  │         ▼                                                       │
  │  Store OCR text → S3: {user_id}/{file_id}/ocr.txt               │
  │  Update DynamoDB: ocr_status = COMPLETED                        │
  │                                                                 │
  │         │                                                       │
  │         ▼                                                       │
  │  GPT Structured Extraction                                      │
  │  Input: OCR text                                                │
  │  Output: JSON { document_category, document_metadata,           │
  │           patient_details, diagnoses, medications, ... }        │
  │  Temperature: 0.1 (factual extraction)                          │
  │                                                                 │
  │         │                                                       │
  │         ▼                                                       │
  │  Store in DynamoDB: structured_data (JSON string)                │
  │  Update: structured_status = COMPLETED, document_category       │
  │                                                                 │
  └─────────────────────────────────────────────────────────────────┘
```

If OCR fails → `ocr_status = FAILED`. If extraction fails → `structured_status = FAILED`. The document is still available for download/delete — it just won't be used in prescription generation.

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_REGION` | `ap-south-1` | AWS region |
| `S3_BUCKET` | `kokoro-doctor` | S3 bucket for file storage |
| `DOCUMENTS_TABLE` | `MedilockerDocuments` | DynamoDB table name |
| `OPENAI_API_KEY` | — | OpenAI API key for GPT extraction |
| `PRESCRIPTION_MAX_DOCS` | `10` | Max documents to use for prescription |

### Upload Validation Constants (`config.py`)

| Constant | Value | Description |
|----------|-------|-------------|
| `ALLOWED_EXTENSIONS` | `pdf, jpg, jpeg, png, heic, heif, webp, tiff, tif, bmp` | Accepted file types |
| `MAX_FILE_SIZE_BYTES` | `10 MB` | Max decoded file size |

---

## Security

- **CORS:** `https://kokoro.doctor` and `http://localhost:8081`
- **User isolation:** S3 paths and DynamoDB queries are scoped to `user_id`
- **Download links:** Presigned URLs expire after 1 hour
- **Owner check:** `get_document_by_file_id` verifies `user_id` matches the document owner
- **Custom exception handler:** CORS headers are preserved on error responses

---

## Error Handling

- **HTTP exceptions** re-raise through the router so status codes (400, 404, 500) propagate correctly.
- **Custom exception handler** in `main.py` ensures CORS headers on error responses.
- **Background OCR failures** are logged and the DynamoDB record is marked `FAILED` — they never bubble up to the user.
- **Orphaned records** (DynamoDB entry exists but S3 object was deleted) are auto-cleaned during list.
- **Frontend** surfaces backend `detail` messages on errors instead of generic fallback text.

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `fastapi` | Web framework |
| `mangum` | AWS Lambda adapter for ASGI |
| `boto3` | AWS SDK (S3, DynamoDB, Textract) |
| `pydantic` | Request validation |
| `openai` | GPT-4o API client |
| `colorlog` | Structured logging |

---

## Related Docs

| Doc | Contents |
|-----|----------|
| `IMPROVEMENTS.md` | Change log for all 7 improvements implemented |
| `PRESCRIPTION_GENERATION.md` | Deep-dive into the prescription pipeline stages |
| `PRESCRIPTION_FORMATTING.md` | Formatting rules for prescription output |
| `FETCH_FILES_ENDPOINT.md` | List-files endpoint: flow, category filter, response format |
