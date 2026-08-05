# Medilocker Services — Detailed Reference

Reference for all service modules in `app/services/`. Each file’s purpose, functions, and logic are described.

---

## Directory Layout

```
app/services/
├── __init__.py
├── prescription_service.py
├── extraction_service.py
├── context_service.py
├── clinical_query_service.py
├── file_service.py
├── document_db_service.py
├── ocr_service.py
├── insurance_extraction_service.py
└── SERVICES_REFERENCE.md   (this file)
```

---

## 1. prescription_service.py

**Purpose:** Prescription synthesis from patient context. Handles only the final GPT step; OCR and extraction are in other services.

### Functions

| Function                                                                     | Type       | Description                                                                                            |
| ---------------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------ |
| `_require_openai_key()`                                                      | private    | Raises `HTTPException` 500 if `OPENAI_API_KEY` is not set                                              |
| `_build_prescription_result(prescription_text, patient_context, log_prefix)` | private    | Builds response dict with `prescription` and optional `patient_details` (name, age, gender, diagnosis) |
| `_generate_prescription_from_context(patient_context, client)`               | private    | Calls GPT-4o to generate prescription text from patient context                                        |
| `generate_prescription_from_context(patient_context)`                        | **public** | Main entry: validates key, runs synthesis, returns `{prescription, patient_details?}`                  |

### Logic

- Uses `extract_patient_details_from_context` from `context_service` to derive patient details.
- GPT prompt enforces: Clinical Summary, Diagnosis, Key Findings, Medications, Advice, Follow-up.
- Returns `{"prescription": str}` or `{"prescription": str, "patient_details": {...}}`.

### Dependencies

- `context_service` (extract_patient_details_from_context)
- `app.config` (OPENAI_API_KEY)
- `openai`, `fastapi`

---

## 2. extraction_service.py

**Purpose:** OCR and structured data extraction pipeline. Contains all GPT extraction logic and the direct file flow. Images only (no PDF).

### Functions

| Function                                                          | Type       | Description                                                                |
| ----------------------------------------------------------------- | ---------- | -------------------------------------------------------------------------- |
| `_truncate_for_log(text, max_len)`                                | private    | Truncates text for logs, appends `"... [truncated, N total chars]"`        |
| `_extract_structured_data_from_text(ocr_text, client)`            | private    | GPT-4o extraction from OCR text → structured JSON                          |
| `extract_structured_data_for_document(ocr_text)`                  | **public** | Single-document extraction entry point (used at upload time)               |
| `_extract_ocr_from_file(file_data)`                               | private    | Runs OCR on one image file. Returns `{filename, text}` or `None`           |
| `_parallel_extract_from_ocr_texts(ocr_texts, client, log_prefix)` | private    | Runs parallel GPT extraction on OCR texts. Returns list of structured data |
| `extract_structured_data_from_ocr_texts(ocr_texts)`               | **public** | v2 flow: OCR texts → GPT extraction → context → prescription               |
| `extract_structured_data_from_files(files)`                       | **public** | Full flow: files → OCR → GPT extraction → context → prescription           |

### Logic

- **`_extract_structured_data_from_text`:** GPT-4o with schema for document_category, patient_details, diagnoses, medications, lab_values, etc. Validates output via `StructuredMedicalData`.
- **`extract_structured_data_for_document`:** Wraps `_extract_structured_data_from_text` with OpenAI client creation. Called at upload time by `file_service`.
- **`_extract_ocr_from_file`:** Checks extension against `ALLOWED_EXTENSIONS`, decodes base64, calls `ocr_service.extract_text_from_file`.
- **`_parallel_extract_from_ocr_texts`:** Uses `ThreadPoolExecutor` to call `_extract_structured_data_from_text` per document.
- **`extract_structured_data_from_files`:** Validates extensions, runs OCR in parallel, then extraction, builds context via `context_service`, calls `prescription_service.generate_prescription_from_context`.
- **`extract_structured_data_from_ocr_texts`:** Same as above but skips OCR step.

### Dependencies

- `ocr_service` (extract_text_from_file)
- `context_service` (build_patient_context, documents_from_extracted_data)
- `prescription_service` (generate_prescription_from_context)
- `app.models.structured_data` (StructuredMedicalData)
- `app.config` (OPENAI_API_KEY, PRESCRIPTION_MAX_DOCS, ALLOWED_EXTENSIONS)
- `openai`, `fastapi`

---

## 3. context_service.py

**Purpose:** Pure context-building logic with no GPT calls. Used by prescription, clinical query, and extraction flows.

### Functions

| Function                                                | Type       | Description                                                                      |
| ------------------------------------------------------- | ---------- | -------------------------------------------------------------------------------- |
| `build_patient_context(documents)`                      | **public** | Builds chronological patient context from document records                       |
| `documents_from_extracted_data(extracted_data_list)`    | **public** | Converts in-memory extracted data to document format for `build_patient_context` |
| `extract_patient_details_from_context(patient_context)` | **public** | Extracts name, age, gender, primary diagnosis from context                       |

### Logic

- **`build_patient_context`:** Parses `structured_data` (JSON string or dict), merges patient_summary, builds `document_history` sorted by `created_at` (newest first).
- **`documents_from_extracted_data`:** Maps `{structured_data, created_at, document_category, file_id}` for each extracted item.
- **`extract_patient_details_from_context`:** Takes first diagnosis from document history, merges with patient_summary.

### Dependencies

- `app.logger`
- No external library dependencies (pure Python + json)

---

## 4. clinical_query_service.py

**Purpose:** Answers doctor questions about patient history using GPT.

### Functions

| Function                                                   | Type       | Description                         |
| ---------------------------------------------------------- | ---------- | ----------------------------------- |
| `answer_clinical_query(patient_context, question, client)` | **public** | Returns `{answer: str}` from GPT-4o |

### Logic

- Uses a clinical assistant system prompt.
- Sends `{doctor_question, patient_context}` as JSON.
- Uses `to_http_exception` for error handling.

### Dependencies

- `app.utils.openai_errors` (to_http_exception)
- `openai`

---

## 5. file_service.py

**Purpose:** S3 and upload orchestration. DynamoDB is the source of truth for metadata.

### Functions

| Function                                        | Type       | Description                                                                   |
| ----------------------------------------------- | ---------- | ----------------------------------------------------------------------------- |
| `upload_files(user_id, files)`                  | **public** | Validates, uploads to S3, creates DynamoDB record, runs OCR and extraction    |
| `_run_and_store_ocr(...)`                       | private    | Runs OCR, stores text in S3, updates DynamoDB, triggers structured extraction |
| `_run_and_store_structured_extraction(...)`     | private    | Runs GPT extraction, stores structured_data in DynamoDB                       |
| `fetch_ocr_text(s3_ocr_key)`                    | **public** | Downloads OCR text from S3 (currently unused)                                 |
| `fetch_files(user_id, category)`                | **public** | Lists files for a user, optionally filtered by document_category              |
| `_get_document_for_operation(user_id, file_id)` | private    | Fetches document by file_id, raises 404 if not found                          |
| `generate_download_link(user_id, file_id)`      | **public** | Returns presigned S3 URL for download                                         |
| `download_file_content(user_id, file_id)`       | **public** | Returns raw file bytes from S3 (currently unused)                             |
| `delete_file(user_id, file_id)`                 | **public** | Deletes S3 objects and DynamoDB record                                        |

### Logic

- **Upload:** Validates extension (`ALLOWED_EXTENSIONS`) and size (10 MB), stores in `Medilocker/Users/{user_id}/{file_id}/original.{ext}`, creates DynamoDB record, runs OCR and extraction synchronously.
- **OCR:** Uses `extract_text_from_file`, stores result in `ocr.txt` in S3.
- **Extraction:** Uses `extraction_service.extract_structured_data_for_document`, stores JSON in DynamoDB.
- **Fetch files:** Queries DynamoDB, checks S3 existence (HEAD), removes orphaned records.
- **Delete:** Lists objects under prefix, batch-deletes from S3, deletes DynamoDB record.

### Dependencies

- `document_db_service`
- `extraction_service` (extract_structured_data_for_document)
- `ocr_service` (extract_text_from_file)
- `app.config` (s3_client, S3_BUCKET, S3_FOLDER_PREFIX, ALLOWED_EXTENSIONS, MAX_FILE_SIZE_BYTES)

---

## 6. document_db_service.py

**Purpose:** DynamoDB access for `MedilockerDocuments`. PK: `user_id`, SK: `created_at`.

### Functions

| Function                                                | Type       | Description                                                       |
| ------------------------------------------------------- | ---------- | ----------------------------------------------------------------- |
| `create_document_record(document_data)`                 | **public** | Inserts a new document record                                     |
| `update_ocr_status(user_id, file_id, status, ...)`      | **public** | Updates ocr_status (PENDING/COMPLETED/FAILED)                     |
| `update_structured_data(user_id, file_id, status, ...)` | **public** | Updates structured_status and structured_data                     |
| `delete_document_record(user_id, file_id)`              | **public** | Deletes a document record                                         |
| `get_documents_for_user(user_id, category)`             | **public** | Returns all documents for a user, optionally filtered by category |
| `get_latest_documents(user_id, limit)`                  | **public** | Returns N most recent documents                                   |
| `get_document_by_file_id(user_id, file_id)`             | **public** | Looks up a single document by file_id (GSI)                       |

### Logic

- **Create:** Builds item with user_id, created_at, file_id, s3 keys, ocr_status, structured_status, etc.
- **Update OCR:** Uses `update_item` with Key (user_id, created_at).
- **Update structured:** Same pattern; stores structured_data as JSON string.
- **Query:** Uses `KeyConditionExpression` for user_id, `ScanIndexForward=False` for newest first.
- **get_document_by_file_id:** Uses `file_id-index` GSI; falls back to partition query if GSI fails.

### Dependencies

- `app.config` (documents_table)
- `boto3`, `botocore`
- `fastapi`

---

## 7. ocr_service.py

**Purpose:** Text extraction from images via AWS Textract. Images only (no PDF).

### Functions

| Function                                                     | Type       | Description                                                          |
| ------------------------------------------------------------ | ---------- | -------------------------------------------------------------------- |
| `extract_text_from_image(image_bytes)`                       | **public** | Extracts text from image bytes using Textract `detect_document_text` |
| `extract_text_from_file(file_bytes, filename, content_type)` | **public** | Wrapper that delegates to `extract_text_from_image`                  |

### Logic

- Uses `textract_client.detect_document_text(Document={'Bytes': image_bytes})`.
- Parses LINE blocks from response and joins text.
- Raises `HTTPException` on Textract or other errors.

### Dependencies

- `app.config` (AWS_REGION)
- `boto3`, `botocore`
- `fastapi`

---

## 8. insurance_extraction_service.py

**Purpose:** Insurance-only pipeline: OCR (image or PDF via Textract) → Groq structured extraction → claim analysis → `document_db_service` save. Used by `POST /medilocker/users/{user_id}/insurance/analyze` (multipart `file`). Does not use `extraction_service` or prescription flows.

**Key entry points:** `extract_insurance_data_from_file(file_bytes, filename, user_id)`, `extract_insurance_structured_data_from_text`, `analyze_insurance_claim`.

---

## 9. **init**.py

**Purpose:** Package marker. Contains only a comment: `# Services module for business logic`.

---

## Service Dependencies Diagram

```
                    ┌─────────────────────┐
                    │  medilocker_router  │
                    └─────────┬──────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌───────────────┐    ┌─────────────────┐    ┌──────────────────────┐
│ file_service  │    │extraction_service│    │prescription_service  │
└───────┬───────┘    └────────┬────────┘    └──────────┬───────────┘
        │                     │                        │
        ├──────────┐          ├── context_service      ├── context_service
        │          │          │   (build_patient_ctx,  │   (extract_patient_
        │          │          │    docs_from_extracted) │    details_from_ctx)
        ▼          ▼          ├── prescription_service │
┌──────────┐  ┌──────────┐   ├── ocr_service          │
│doc_db_svc│  │extraction │   └── StructuredMedical... │
└──────────┘  │_service   │                            │
              │(extract_  │   ┌─────────────────────┐  │
              │struct_for │   │clinical_query_service│  │
              │_document) │   └─────────┬───────────┘  │
              └──────────┘              │              │
                    │                   ▼              │
                    ▼          ┌───────────────┐       │
              ┌──────────┐    │context_service │◄──────┘
              │ocr_service│    └───────────────┘
              └──────────┘
```

---

## Data Flow Summary

| Flow                            | Services Used                                                           |
| ------------------------------- | ----------------------------------------------------------------------- |
| **Upload**                      | file_service → ocr_service, extraction_service, document_db_service     |
| **Prescription (stored docs)**  | document_db_service → context_service → prescription_service            |
| **Prescription (direct files)** | extraction_service → ocr_service, context_service, prescription_service |
| **Clinical query**              | document_db_service → context_service → clinical_query_service          |
| **List files**                  | file_service → document_db_service                                      |
| **Download**                    | file_service → document_db_service                                      |
| **Delete**                      | file_service → document_db_service                                      |
| **Insurance extract**           | insurance_extraction_service → ocr_service, document_db_service (Groq)    |
