# Medilocker Lambda — Directory Structure & Module Reference

Overview of the Medilocker Lambda codebase: directory layout, file contents, functions, and logic.

---

## Directory Structure

```
medilocker_lambda/
├── app/
│   ├── main.py                    # FastAPI app, CORS, Lambda handler
│   ├── config.py                  # Environment & AWS config
│   ├── logger.py                  # Logging setup
│   │
│   ├── models/
│   │   ├── schemas.py             # Pydantic request/response models
│   │   └── structured_data.py     # Medical data extraction schema
│   │
│   ├── routers/
│   │   └── medilocker_router.py   # API endpoints
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── prescription_service.py    # Prescription synthesis from context
│   │   ├── extraction_service.py      # OCR + document extraction pipeline
│   │   ├── context_service.py         # Patient context building (no GPT calls)
│   │   ├── clinical_query_service.py  # Doctor Q&A over patient history
│   │   ├── file_service.py            # S3 upload, list, download, delete
│   │   ├── document_db_service.py     # DynamoDB CRUD for documents
│   │   ├── ocr_service.py             # AWS Textract OCR
│   │   └── SERVICES_REFERENCE.md     # Detailed service reference
│   │
│   └── utils/
│       ├── __init__.py
│       └── openai_errors.py        # OpenAI exception → HTTPException
│
├── docs/
│   ├── MEDILOCKER_FLOW.md         # Medilocker flow documentation
│   ├── MEDILOCKER_STRUCTURE.md    # This file
│   ├── PRESCRIPTION_GENERATION.md # Prescription generation flow
│   ├── CLINICAL_QUERY.md          # Clinical Q&A endpoint (separate from prescription)
│   ├── CLINICAL_QUERY_HISTORY.md  # Design: clinical query follow-up history
│   └── FETCH_FILES_ENDPOINT.md    # Fetch files endpoint docs
└── requirements.txt
```

---

## File Reference

### `app/main.py`

**Purpose:** FastAPI app entry point, CORS, and AWS Lambda handler.

| Item | Description |
|------|-------------|
| **Functions** | `custom_http_exception_handler` |
| **Logic** | Creates FastAPI app, adds CORS for kokoro.doctor and localhost, custom HTTP exception handler that preserves CORS headers, mounts medilocker router, exposes `handler` for Mangum (Lambda). |

---

### `app/config.py`

**Purpose:** Central configuration from environment variables.

| Item | Description |
|------|-------------|
| **Exports** | `AWS_REGION`, `S3_BUCKET`, `S3_FOLDER_PREFIX`, `s3_client`, `dynamodb`, `DOCUMENTS_TABLE`, `documents_table`, `OPENAI_API_KEY`, `PRESCRIPTION_MAX_DOCS`, `ALLOWED_EXTENSIONS`, `MAX_FILE_SIZE_BYTES` |
| **Logic** | Loads AWS region, S3 bucket, DynamoDB table name, OpenAI key, prescription doc limit (default 10), allowed file extensions, and max file size (10 MB). |

---

### `app/logger.py`

**Purpose:** Shared logging configuration.

| Item | Description |
|------|-------------|
| **Functions** | `get_logger(name)` |
| **Logic** | Configures root logger with colored output, sets log level from `LOG_LEVEL`, reduces boto3 verbosity. |

---

### `app/models/schemas.py`

**Purpose:** Pydantic models for API requests.

| Item | Description |
|------|-------------|
| **Classes** | `FileUploadModel`, `UploadRequest`, `UserRequest`, `FileRequest`, `ExtractionRequest`, `ClinicalQueryRequest` |
| **Logic** | Defines request shapes: file upload (filename, base64 content, metadata), upload request (user_id + files), extraction request (files + optional patient details), clinical query (question). |

---

### `app/models/structured_data.py`

**Purpose:** Pydantic schema for GPT-extracted medical data.

| Item | Description |
|------|-------------|
| **Classes** | `DocumentMetadata`, `PatientDetails`, `StructuredMedicalData` |
| **Logic** | Models for document metadata (date, doctor, hospital), patient details (name, age, gender), and full structured medical data (diagnoses, medications, lab values, etc.). |

---

### `app/routers/medilocker_router.py`

**Purpose:** HTTP endpoints for Medilocker.

| Item | Description |
|------|-------------|
| **Functions** | `upload_file`, `fetch_files`, `generate_download_link`, `delete_file`, `generate_prescription_from_s3_files`, `clinical_query`, `extract_structured_data` |
| **Logic** | Thin router that delegates to services. Handles: upload, list files (with optional category filter), presigned download URL, delete, prescription from stored docs (Flow A), clinical Q&A, and prescription from direct file upload (Flow B). |

| Endpoint | Handler | Service |
|----------|---------|---------|
| `POST /medilocker/upload` | `upload_file` | file_service |
| `GET /medilocker/users/{user_id}/files` | `fetch_files` | file_service |
| `GET /medilocker/users/{user_id}/files/{file_id}/download` | `generate_download_link` | file_service |
| `DELETE /medilocker/users/{user_id}/files/{file_id}` | `delete_file` | file_service |
| `POST /medilocker/users/{user_id}/prescription` | `generate_prescription_from_s3_files` | document_db, context_service, prescription_service |
| `POST /medilocker/users/{user_id}/clinical-query` | `clinical_query` | document_db, context_service, clinical_query_service |
| `POST /medilocker/prescription` | `extract_structured_data` | extraction_service |

---

### `app/services/prescription_service.py`

**Purpose:** Prescription synthesis from patient context only.

| Item | Description |
|------|-------------|
| **Functions** | `_require_openai_key`, `_build_prescription_result`, `_generate_prescription_from_context`, `generate_prescription_from_context` |
| **Logic** | **Helpers:** require OpenAI key, build prescription + patient_details response. **Synthesis:** GPT prescription from patient context. Handles only the final synthesis step; OCR and extraction are in extraction_service. |

---

### `app/services/extraction_service.py`

**Purpose:** All structured data extraction logic (GPT calls) and the OCR pipeline for the direct file flow. Images only (no PDF).

| Item | Description |
|------|-------------|
| **Functions** | `_extract_structured_data_from_text`, `extract_structured_data_for_document`, `_truncate_for_log`, `_extract_ocr_from_file`, `_parallel_extract_from_ocr_texts`, `extract_structured_data_from_ocr_texts`, `extract_structured_data_from_files` |
| **Logic** | **Extraction:** GPT-4o structured extraction from OCR text (single doc and parallel). **OCR:** extract from single image file (bytes). **Pipeline:** OCR → extract → build context → prescription_service.generate_prescription_from_context. Images only (no PDF). |

---

### `app/services/context_service.py`

**Purpose:** Pure context-building logic with no GPT calls. Used by prescription, clinical query, and extraction flows.

| Item | Description |
|------|-------------|
| **Functions** | `build_patient_context`, `documents_from_extracted_data`, `extract_patient_details_from_context` |
| **Logic** | **Context:** build chronological patient context from documents; convert extracted data to document format. **Details:** extract name, age, gender, primary diagnosis from context. No OpenAI or external API dependencies. |

---

### `app/services/clinical_query_service.py`

**Purpose:** Answer doctor questions from patient history.

| Item | Description |
|------|-------------|
| **Functions** | `answer_clinical_query` |
| **Logic** | Takes patient context and question, calls GPT-4o with clinical assistant prompt, returns factual answer. Uses `to_http_exception` for error handling. |

---

### `app/services/file_service.py`

**Purpose:** S3 and upload orchestration.

| Item | Description |
|------|-------------|
| **Functions** | `upload_files`, `_run_and_store_ocr`, `_run_and_store_structured_extraction`, `fetch_ocr_text`, `fetch_files`, `_get_document_for_operation`, `generate_download_link`, `download_file_content`, `delete_file` |
| **Logic** | **Upload:** validate, store in S3, create DynamoDB record, run OCR (Textract), store OCR in S3, run GPT extraction, store structured_data in DynamoDB. **List:** query DynamoDB by user (and optional category). **Download:** presigned URL or raw bytes. **Delete:** remove S3 objects and DynamoDB record. |

---

### `app/services/document_db_service.py`

**Purpose:** DynamoDB access for Medilocker documents.

| Item | Description |
|------|-------------|
| **Functions** | `create_document_record`, `update_ocr_status`, `update_structured_data`, `delete_document_record`, `get_documents_for_user`, `get_latest_documents`, `get_document_by_file_id` |
| **Logic** | **Create:** insert document metadata (user_id, file_id, s3 keys, ocr_status, etc.). **Update:** set ocr_status, structured_status, structured_data. **Read:** list by user (with category filter), get latest N, get by file_id. **Delete:** remove record. PK=user_id, SK=created_at. |

---

### `app/services/ocr_service.py`

**Purpose:** Text extraction via AWS Textract.

| Item | Description |
|------|-------------|
| **Functions** | `extract_text_from_image`, `extract_text_from_file` |
| **Logic** | **Images:** `detect_document_text` on bytes. **Unified:** `extract_text_from_file` delegates to `extract_text_from_image`. Images only (no PDF). |

---

### `app/utils/openai_errors.py`

**Purpose:** Map OpenAI errors to HTTP responses.

| Item | Description |
|------|-------------|
| **Functions** | `to_http_exception` |
| **Logic** | Converts RateLimitError → 429, AuthenticationError → 503, APIError → 503; re-raises HTTPException; logs and returns generic 503 for other errors. |

---

## Data Flow Summary

```
UPLOAD FLOW:
  Client → upload_file → file_service.upload_files
    → S3 (original) + DynamoDB (record)
    → _run_and_store_ocr (Textract → S3 ocr.txt)
    → _run_and_store_structured_extraction (GPT → DynamoDB structured_data)

PRESCRIPTION FLOW A (from stored docs):
  Client → generate_prescription_from_s3_files
    → document_db_service.get_latest_documents
    → context_service.build_patient_context
    → prescription_service.generate_prescription_from_context

PRESCRIPTION FLOW B (direct upload):
  Client → extract_structured_data → extraction_service.extract_structured_data_from_files
    → OCR (Textract) per file
    → GPT extraction per document
    → context_service.documents_from_extracted_data + build_patient_context
    → prescription synthesis

CLINICAL QUERY FLOW:
  Client → clinical_query
    → document_db_service.get_latest_documents
    → context_service.build_patient_context
    → clinical_query_service.answer_clinical_query
```

---

## Dependencies Between Services

```
medilocker_router
  ├── file_service
  ├── prescription_service
  │     └── context_service (extract_patient_details_from_context)
  ├── extraction_service
  │     ├── context_service (build_patient_context, documents_from_extracted_data)
  │     ├── prescription_service (generate_prescription_from_context)
  │     └── ocr_service (extract_text_from_file)
  ├── document_db_service
  ├── context_service
  └── clinical_query_service

file_service
  ├── document_db_service
  ├── extraction_service (extract_structured_data_for_document)
  └── ocr_service
```

---

## Related Documentation

| File | Description |
|------|-------------|
| `CLINICAL_QUERY.md` | Clinical Q&A endpoint — doctor questions over patient records |
| `CLINICAL_QUERY_HISTORY.md` | Design for clinical query follow-up history (DynamoDB table, session_id) |
| `PRESCRIPTION_GENERATION.md` | Prescription generation flow (Flow A & B) |
| `MEDILOCKER_FLOW.md` | Medilocker flow documentation |
| `FETCH_FILES_ENDPOINT.md` | Fetch files endpoint documentation |
| `app/services/SERVICES_REFERENCE.md` | Detailed reference for all service modules, functions, and logic |
