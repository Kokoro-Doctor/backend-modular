# Prescription & Full Case Analysis — End-to-End System Guide

This document describes how the Kokoro Doctor system works for medical document management, prescription generation, and full case analysis — from document addition through to final output.

---

## Table of Contents

1. [High-Level Architecture](#high-level-architecture)
2. [Document Addition Flow](#document-addition-flow)
3. [Upload-Time Processing Pipeline](#upload-time-processing-pipeline)
4. [Storage & Data Model](#storage--data-model)
5. [Prescription Generation](#prescription-generation)
6. [Full Case Analysis & Clinical Query](#full-case-analysis--clinical-query)
7. [Frontend Flows](#frontend-flows)
8. [API Reference](#api-reference)
9. [Configuration & Dependencies](#configuration--dependencies)

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              KOKORO DOCTOR SYSTEM                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                   │
│   PATIENT SIDE                          DOCTOR SIDE                               │
│   ───────────                          ───────────                               │
│                                                                                   │
│   Medilocker.jsx                      GeneratePrescription.jsx                   │
│   UserDashboard.jsx                   Prescription.jsx (direct upload)            │
│   FullCaseAnalysis.jsx                PrescriptionPreview.jsx                     │
│        │                                     │                                    │
│        │  upload / fetch / download           │  fetch files / generate Rx       │
│        ▼                                     ▼                                    │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                    API Gateway  /medilocker/*                             │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                        │                                          │
│                                        ▼                                          │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │              Medilocker Lambda (FastAPI + Mangum)                          │   │
│   │  • file_service      • prescription_service  • document_db_service       │   │
│   │  • ocr_service       • medilocker_router                                 │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                        │                                          │
│        ┌───────────────────────────────┼───────────────────────────────┐        │
│        ▼                               ▼                               ▼        │
│   ┌─────────┐                   ┌─────────────┐                   ┌──────────┐   │
│   │   S3    │                   │  DynamoDB   │                   │ OpenAI   │   │
│   │ (files, │                   │ Medilocker- │                   │ GPT-4o   │   │
│   │  OCR)   │                   │  Documents  │                   │ Textract │   │
│   └─────────┘                   └─────────────┘                   └──────────┘   │
│                                                                                   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**Stack:** FastAPI + Mangum (AWS Lambda), S3 (file storage), DynamoDB (metadata), AWS Textract (OCR), OpenAI GPT-4o (extraction + synthesis).

---

## Document Addition Flow

### 1. Patient Uploads Documents

**Entry points:**
- **Medilocker** (`Medilocker.jsx`) — Patient’s main document storage screen
- **UserDashboard** — Quick upload from dashboard

**Flow:**
1. Patient selects file(s) via `expo-document-picker`
2. File is converted to base64
3. Request sent to `POST /medilocker/upload`:

```json
{
  "user_id": "usr_xxx",
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

**Validation:**
- Allowed extensions: `pdf, jpg, jpeg, png, heic, heif, webp, tiff, tif, bmp`
- Max file size: 10 MB (decoded)
- Invalid base64 → 400

### 2. Backend Processing (Synchronous)

All processing runs **synchronously** before the HTTP response. Lambda freezes after the handler returns, so background work would not complete.

```
Client sends upload
       │
       ▼
Validate extension & size
       │
       ▼
Generate file_id (8-char UUID)
       │
       ▼
PUT original file → S3: Medilocker/Users/{user_id}/{file_id}/original.{ext}
       │
       ▼
Create DynamoDB record (ocr_status=PENDING, structured_status=PENDING)
       │
       ▼
┌──────────────────────────────────────────────────────────────────┐
│  STAGE A: OCR (AWS Textract)                                       │
│  • Images → detect_document_text                                   │
│  • PDFs   → analyze_document (via S3 object reference)             │
│  → Store OCR text → S3: {user_id}/{file_id}/ocr.txt                │
│  → Update DynamoDB: ocr_status = COMPLETED                          │
└──────────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────────┐
│  STAGE B: GPT Structured Extraction                               │
│  • Input: OCR text                                                │
│  • Model: gpt-4o, Temp: 0.1                                        │
│  • Output: JSON (patient_details, medications, diagnoses, etc.)    │
│  → Store in DynamoDB: structured_data (JSON string)                │
│  → Update: structured_status = COMPLETED, document_category       │
└──────────────────────────────────────────────────────────────────┘
       │
       ▼
Return 200 { "message": "Files uploaded successfully" }
```

If OCR fails → `ocr_status = FAILED`. If extraction fails → `structured_status = FAILED`. The document stays in storage but is excluded from prescription generation.

---

## Upload-Time Processing Pipeline

### OCR (Textract)

| File Type | Method |
|-----------|--------|
| Images (jpg, png, etc.) | `detect_document_text` (bytes) |
| PDFs | `analyze_document` (S3 object reference) |

### Structured Extraction Schema

GPT extracts a strict JSON schema per document:

```json
{
  "document_category": "LAB_REPORT | SCAN_REPORT | HEALTH_INSURANCE | HOSPITAL_RECORD | OTHER",
  "document_metadata": {
    "document_date": null,
    "doctor_name": null,
    "hospital_name": null,
    "department": null
  },
  "patient_details": { "name": null, "age": null, "gender": null },
  "diagnoses": [],
  "symptoms": [],
  "medical_conditions": [],
  "medications": [
    { "name": "", "dose": null, "frequency": null, "duration": null }
  ],
  "tests": [],
  "lab_values": [
    { "test_name": "", "value": null, "unit": null, "reference_range": null }
  ],
  "medical_history": [],
  "clinical_context": [],
  "document_summary": ""
}
```

`document_category` is used for filtering in list-files (e.g. `?category=LAB_REPORT`).

---

## Storage & Data Model

### S3 Layout

```
kokoro-doctor/
└── Medilocker/Users/
    └── {user_id}/
        └── {file_id}/
            ├── original.{ext}   ← uploaded file
            └── ocr.txt         ← extracted text
```

### DynamoDB — `MedilockerDocuments`

| Attribute | Type | Description |
|-----------|------|-------------|
| **user_id** (PK) | String | User identifier |
| **created_at** (SK) | String | ISO 8601 timestamp |
| file_id | String | 8-char UUID (GSI: file_id-index) |
| filename | String | Original display filename |
| doc_type | String | File type (e.g. pdf, jpg) |
| s3_original_key | String | S3 key to uploaded file |
| s3_ocr_key | String | S3 key to ocr.txt |
| ocr_status | String | PENDING / COMPLETED / FAILED |
| structured_data | String | JSON string — GPT-extracted medical data |
| structured_status | String | PENDING / COMPLETED / FAILED |
| document_category | String | LAB_REPORT, SCAN_REPORT, etc. |
| file_metadata | Map | Client metadata (file_type, file_size, dates) |

---

## Prescription Generation

There are **two flows**:

| Flow | Endpoint | Use Case | GPT Calls |
|------|----------|----------|-----------|
| **A** | `POST /medilocker/users/{user_id}/prescription` | Patient’s stored docs | 1 (synthesis only) |
| **B** | `POST /medilocker/prescription` | Doctor uploads files on-the-fly (no storage) | N+1 (extraction + synthesis) |

### Flow A — From Stored Documents (Primary)

Used when the doctor generates a prescription from a patient’s existing Medilocker documents (e.g. in **GeneratePrescription.jsx**).

**Steps:**
1. Query DynamoDB: `get_latest_documents(user_id, limit=PRESCRIPTION_MAX_DOCS)` (newest first)
2. Filter: `ocr_status == COMPLETED` AND `structured_status == COMPLETED`
3. Parse `structured_data` JSON from each document
4. Build patient context (chronological document history)
5. Single GPT synthesis call → final prescription text

**No OCR, no per-document extraction** — all heavy work was done at upload time.

**Response:**
```json
{
  "prescription": "Clinical Summary:\n...\n\nMedications:\n...",
  "patient_details": {
    "name": "John Doe",
    "age": 45,
    "gender": "Male",
    "diagnosis": "Type 2 Diabetes"
  }
}
```

### Flow B — Direct Extraction (Stateless)

Used when the doctor uploads files directly in **Prescription.jsx** without storing them in Medilocker.

**Request:**
```json
{
  "files": [
    { "filename": "scan.jpg", "content": "<base64>" }
  ]
}
```

**Pipeline:**
1. Parallel OCR (Textract) on all files
2. Parallel GPT extraction per document
3. Merge structured data (Python, no GPT)
4. Single GPT synthesis → prescription text

**Response:** Same as Flow A.

### Synthesis Output Structure

The final prescription includes (non-empty only):
1. **Clinical Summary** (mandatory, 4–5 lines)
2. **Diagnosis** (if supported by data)
3. **Key Findings**
4. **Medications**
5. **Advice / Lifestyle Recommendations**
6. **Follow-up Recommendations**

---

## Full Case Analysis & Clinical Query

### Clinical Query Endpoint

```
POST /medilocker/users/{user_id}/clinical-query
```

**Request:**
```json
{
  "question": "What are the patient's lipid trends over the last 6 months?"
}
```

**Purpose:** Lets the doctor ask natural-language questions about the patient’s stored medical records. The system uses the same patient context (built from `structured_data`) and answers using GPT-4o.

**Flow:**
1. Query DynamoDB for latest documents (same as prescription)
2. Filter: `ocr_status == COMPLETED` AND `structured_status == COMPLETED`
3. Build patient context via `build_patient_context()`
4. Call `answer_clinical_query(patient_context, question, client)`
5. GPT returns a concise, factual answer

**Response:**
```json
{
  "answer": "Based on the available documents, the patient's LDL has..."
}
```

**Rules enforced by prompt:**
- Use ONLY provided information
- Never hallucinate diagnoses or medications
- If information is missing, say it is not available
- Prefer newer documents when conflicts exist
- Be concise and clinical

### Full Case Analysis Screen

**FullCaseAnalysis.jsx** provides:
- Filter tabs: All, Prescription, Scan Reports, Lab Reports, Hospital History, Health Insurance
- Document list (by category)
- “Clinical AI Assistant” button (intended to integrate with clinical-query)
- “Generate Prescription” button

The screen is wired for full case analysis; the clinical-query API is available for integration.

---

## Frontend Flows

### Patient Flow

| Screen | Actions |
|--------|---------|
| **Medilocker** | Upload, list, download, delete, share documents |
| **UserDashboard** | List, download, delete, share |
| **FullCaseAnalysis** | View documents by category, generate prescription, clinical AI |

**MedilockerService.js** functions:
- `FetchFromServer(userId)` → `GET /users/{userId}/files`
- `upload(payload)` → `POST /upload`
- `download(userId, fileId)` → `GET /users/{userId}/files/{fileId}/download`
- `remove(userId, fileId)` → `DELETE /users/{userId}/files/{fileId}`
- `extractStructuredData(files)` → `POST /prescription` (Flow B)

### Doctor Flow

| Screen | Actions |
|--------|---------|
| **GeneratePrescription** | View patient’s Medilocker files, generate prescription from stored docs (Flow A), navigate to PrescriptionPreview |
| **Prescription.jsx** | Doctor uploads files directly → `extractStructuredData()` (Flow B) → suggested prescription |
| **PrescriptionPreview** | View, edit, download prescription as PDF |

**GeneratePrescription flow:**
1. Fetch user, appointment, and Medilocker files
2. Doctor clicks “Generate Prescription”
3. `POST /medilocker/users/{userId}/prescription`
4. Navigate to PrescriptionPreview with `generatedPrescription`

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/medilocker/upload` | Upload files (runs OCR + extraction inline) |
| GET | `/medilocker/users/{user_id}/files` | List files (optional `?category=LAB_REPORT`) |
| GET | `/medilocker/users/{user_id}/files/{file_id}/download` | Presigned download URL |
| DELETE | `/medilocker/users/{user_id}/files/{file_id}` | Delete file and metadata |
| POST | `/medilocker/users/{user_id}/prescription` | Generate prescription from stored docs (Flow A) |
| POST | `/medilocker/users/{user_id}/prescription/save` | Save approved prescription to Medilocker |
| POST | `/medilocker/prescription` | Direct extraction from base64 files (Flow B) |
| POST | `/medilocker/users/{user_id}/clinical-query` | Answer doctor’s question about patient records |

---

## Configuration & Dependencies

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_REGION` | ap-south-1 | AWS region |
| `S3_BUCKET` | kokoro-doctor | S3 bucket |
| `DOCUMENTS_TABLE` | MedilockerDocuments | DynamoDB table |
| `OPENAI_API_KEY` | — | **Required** for extraction and synthesis |
| `PRESCRIPTION_MAX_DOCS` | 10 | Max documents for prescription/clinical-query |

### Upload Limits

| Constant | Value |
|----------|-------|
| `ALLOWED_EXTENSIONS` | pdf, jpg, jpeg, png, heic, heif, webp, tiff, tif, bmp |
| `MAX_FILE_SIZE_BYTES` | 10 MB |

### Performance Summary

| Flow | OCR | GPT Extraction | GPT Synthesis | Typical Latency |
|------|-----|----------------|---------------|-----------------|
| Flow A (stored) | 0 | 0 | 1 | 2–5 s |
| Flow B (direct) | N | N | 1 | 15–60 s |

Flow A is faster and cheaper because OCR and extraction are done once at upload and cached in DynamoDB.

---

## End-to-End Summary

```
DOCUMENT ADDITION                    PRESCRIPTION & CASE ANALYSIS
─────────────────                   ────────────────────────────

Patient uploads file(s)
        │
        ▼
POST /upload
        │
        ├─► S3: original + ocr.txt
        ├─► DynamoDB: metadata + structured_data
        │
        ▼
File ready for prescription
        │
        ├─────────────────────────────────────────────────────────┐
        │                                                          │
        ▼                                                          ▼
Doctor: Generate from stored docs              Doctor: Direct upload
POST /users/{id}/prescription                  POST /prescription
        │                                                          │
        ▼                                                          ▼
Query DynamoDB → build context                 OCR → Extract → Merge
        │                                                          │
        └──────────────────────┬───────────────────────────────────┘
                               │
                               ▼
                        GPT Synthesis
                               │
                               ▼
                        Prescription text
                               │
                               ▼
                        PrescriptionPreview / PDF

        │
        ▼
Doctor: Clinical question
POST /users/{id}/clinical-query
        │
        ▼
Same patient context → GPT answer
```
