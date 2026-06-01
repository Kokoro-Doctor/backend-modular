# Medilocker Lambda

### POST `/medilocker/upload`

Upload medical files to user's medilocker.

```json
{
  "user_id": "USR_12345678-1234-1234-1234-123456789012",
  "files": [
    {
      "filename": "prescription.pdf",
      "content": "JVBERi0xLjQKJeLjz9MKMy...",
      "metadata": {
        "type": "prescription",
        "date": "2025-01-15"
      }
    },
    {
      "filename": "lab_report.jpg",
      "content": "/9j/4AAQSkZJRgABAQAAAQ...",
      "metadata": {
        "type": "lab_report",
        "date": "2025-01-10"
      }
    }
  ]
}
```

**Single file:**

```json
{
  "user_id": "USR_12345678-1234-1234-1234-123456789012",
  "files": [
    {
      "filename": "prescription.pdf",
      "content": "JVBERi0xLjQKJeLjz9MKMy...",
      "metadata": {}
    }
  ]
}
```

---

### GET `/medilocker/users/{user_id}/files`

Fetch list of files for a user. Optionally filter by document category.

**Path Parameters:**

- `user_id`: User ID (required)

**Query Parameters (optional):**

- `category`: Filter by document category. Case-insensitive. Values include `LAB_REPORT`, `SCAN_REPORT`, `HEALTH_INSURANCE`, `HOSPITAL_RECORD`, `PRESCRIPTION`, `INSURANCE_POLICY`, `HOSPITAL_BILL`, `OTHER`.

**Examples:**

```
GET /medilocker/users/USR_12345678-1234-1234-1234-123456789012/files
```

```
GET /medilocker/users/USR_12345678-1234-1234-1234-123456789012/files?category=LAB_REPORT
```

**Response:**

```json
{
  "files": [
    {
      "filename": "prescription.pdf",
      "file_id": "a1b2c3d4",
      "document_category": "HOSPITAL_RECORD",
      "metadata": { "type": "prescription", "date": "2025-01-15" }
    }
  ]
}
```

**Empty response:**

```json
{
  "message": "No files found",
  "files": []
}
```

---

### GET `/medilocker/users/{user_id}/files/{file_id}/download`

Generate presigned download URL for a file. Use `file_id` from the list response.

**Path Parameters:**

- `user_id`: User ID (required)
- `file_id`: File ID from list response (required)

**Example:**

```
GET /medilocker/users/USR_12345678-1234-1234-1234-123456789012/files/a1b2c3d4/download
```

---

### DELETE `/medilocker/users/{user_id}/files/{file_id}`

Delete a file from user's medilocker. Use `file_id` from the list response.

**Path Parameters:**

- `user_id`: User ID
- `file_id`: File ID from list response

**Example:**

```
DELETE /medilocker/users/USR_12345678-1234-1234-1234-123456789012/files/a1b2c3d4
```

---

### POST `/medilocker/users/{user_id}/prescription`

Generate prescription for all documents stored in S3 for a given user_id. Fetches all files from S3, downloads them, and processes through prescription extraction.

**Path Parameters:**

- `user_id`: User ID (required)

**Example:**

```
POST /medilocker/users/USR_12345678-1234-1234-1234-123456789012/prescription
```

**Note:**

- No request body required. The endpoint automatically fetches all files from the user's medilocker in S3.
- Returns 404 if no files are found for the user.
- Processes all files found in the user's medilocker through GPT-4 Vision extraction.

---

### POST `/medilocker/users/{user_id}/prescription/save`

Save an approved prescription to the patient's Medilocker. Stores the prescription as a document that appears in file listings and can be downloaded via the existing download endpoint. Document category is set to `PRESCRIPTION`.

**Path Parameters:**

- `user_id`: Patient's user ID (Medilocker owner)

**Request Body:**

```json
{
  "prescription_pdf": "JVBERi0xLjQKJeLjz9MKMy..."
}
```

**Note:** `prescription_pdf` must be base64-encoded PDF content.

**Example:**

```
POST /medilocker/users/USR_12345678-1234-1234-1234-123456789012/prescription/save
```

**Response:**

```json
{
  "message": "Prescription saved to Medilocker successfully",
  "file_id": "a1b2c3d4",
  "filename": "Prescription_2026-03-09.pdf"
}
```

**Note:** The saved prescription appears in `GET /medilocker/users/{user_id}/files` and can be filtered with `?category=PRESCRIPTION`. It is downloadable via `GET /medilocker/users/{user_id}/files/{file_id}/download`.

---

### POST `/medilocker/users/{user_id}/clinical-query`

Doctor asks a question about the patient's stored medical records. Uses GPT to answer based on pre-extracted structured data from the patient's documents.

**Path Parameters:**

- `user_id`: Patient's user ID (Medilocker owner)

**Request Body:**

```json
{
  "question": "What medications is the patient currently taking?"
}
```

**Response:**

```json
{
  "answer": "Based on the medical records, the patient is currently taking Metformin 500mg BD and Atorvastatin 10mg OD..."
}
```

**Note:** Requires documents with `ocr_status=COMPLETED` and `structured_status=COMPLETED`. Returns "No medical documents available" if no processed documents exist.

---

### POST `/medilocker/users/{user_id}/insurance/autofill-stored`

Autofill an insurance claim form using **already uploaded and OCR-processed** documents for that user (Pipeline 2 — **no file upload**, **no Textract** on this request).

The backend loads the **latest** Medilocker record per category from DynamoDB (`MedilockerDocuments`), reads each document’s `ocr.txt` from S3, then runs the same LLM autofill path as multipart upload autofill (`multi_doc_extractor` → `claim_form_filler`). Typical source of these rows is hospital admission uploads (`INSURANCE_POLICY`, `HOSPITAL_BILL`, `PRESCRIPTION`) with async OCR completing in the background.

**Path parameters:**

- `user_id`: Patient’s Medilocker user ID (required)

**Request body:** none

**Example:**

```
POST /medilocker/users/USR_12345678-1234-1234-1234-123456789012/insurance/autofill-stored
```

**cURL example:**

```bash
curl -X POST "https://<API_BASE>/medilocker/users/USR_12345678-1234-1234-1234-123456789012/insurance/autofill-stored" \
  -H "Authorization: Bearer <JWT_IF_REQUIRED>"
```

**Success (200):** Same overall shape as autofill from `POST /medilocker/insurance/analyze` — notably `flow`, `autofill_extracted`, `autofill_result`, `timings`, `documents_processed`. For stored-doc autofill, `flow` is always `"stored_autofill"`.

```json
{
  "flow": "stored_autofill",
  "autofill_extracted": {},
  "autofill_result": {},
  "error": null,
  "timings": {
    "multi_doc_extraction": 0.0,
    "form_filling": 0.0
  },
  "documents_processed": ["insurance_policy", "hospital_bill", "prescription"]
}
```

On successful stored autofill, the backend also makes a best-effort update to the user's `Users` row when populated diagnosis fields are present under `autofill_extracted.diagnosis_and_procedures`:

- `primary_diagnosis`
- `primary_icd_code`
- `additional_diagnosis`
- `additional_icd_code`
- `diagnosis_updated_at`

This side effect preserves all existing user attributes. If the `Users` update fails, the autofill response still returns successfully and the failure is logged.

**Errors:**

| HTTP | When |
| ---- | ---- |
| `404` | One or more of `INSURANCE_POLICY`, `HOSPITAL_BILL`, `PRESCRIPTION` is missing for this user |
| `409` | All three exist but at least one still has `ocr_status` ≠ `COMPLETED` — retry after OCR finishes (e.g. poll `GET /medilocker/users/{user_id}/files/{file_id}/status`) |
| `500` | Failed to read stored OCR from S3 (`s3_ocr_key`) or unexpected server error |

**Note:** Requires `GROQ_API_KEY` (Groq) for the LLM steps. Does **not** call Textract on this endpoint.

---

### POST `/medilocker/insurance/analyze`

Extract structured insurance claim data and run claim analysis from a single document. **Multipart form upload** (not JSON) — send the file as `multipart/form-data` so large PDFs are not base64-encoded in the body. No `user_id` in the path; the uploaded file is sufficient (stateless, no DynamoDB write).

**Request:** `multipart/form-data`

| Field  | Type | Required | Description                                                                               |
| ------ | ---- | -------- | ----------------------------------------------------------------------------------------- |
| `file` | file | Yes      | Insurance form: PDF or supported image (jpg, jpeg, png, heic, heif, webp, tiff, tif, bmp) |

**cURL example:**

```bash
curl -X POST "https://<API_BASE>/medilocker/insurance/analyze" \
  -H "Authorization: Bearer <JWT_IF_REQUIRED>" \
  -F "file=@/path/to/Claim_Form.pdf"
```

**Response:**

```json
{
  "structured_data": {
    "document_category": "INSURANCE_FORM",
    "patient_details": { "name": null, "age": null, "gender": null },
    "insurance_details": {
      "insurance_company": null,
      "policy_name": null,
      "policy_number": null
    },
    "hospital_details": {
      "hospital_name": null,
      "admission_date": null,
      "discharge_date": null
    },
    "claim_details": {
      "treatment": null,
      "bill_amount": null,
      "claimed_amount": null,
      "documents_submitted": []
    },
    "document_metadata": { "document_date": null },
    "document_summary": "",
    "source_filename": "Claim_Form.pdf"
  },
  "analysis": {
    "is_complete": false,
    "missing_fields": [],
    "issues": [],
    "suggestions": [],
    "claim_opportunity": ""
  }
}
```

- On OCR or structured-extraction failure, `structured_data` and `analysis` may both be `null`.
- **Requirements:** `GROQ_API_KEY` must be configured for Groq (LLM) calls. PDFs use AWS Textract async analysis on a temporary S3 object; the Lambda execution role needs Textract + S3 permissions on the Medilocker bucket.

---

### POST `/medilocker/discharge/analyze`

Extract structured discharge summary data and run patient-oriented analysis from a single document. **Multipart form upload** (not JSON) — same transport as insurance analyze. No `user_id` in the path (stateless).

**Request:** `multipart/form-data`

| Field  | Type | Required | Description                                                                                  |
| ------ | ---- | -------- | -------------------------------------------------------------------------------------------- |
| `file` | file | Yes      | Discharge summary: PDF or supported image (jpg, jpeg, png, heic, heif, webp, tiff, tif, bmp) |

**cURL example:**

```bash
curl -X POST "https://<API_BASE>/medilocker/discharge/analyze" \
  -H "Authorization: Bearer <JWT_IF_REQUIRED>" \
  -F "file=@/path/to/Discharge_Summary.pdf"
```

**Response:**

```json
{
  "structured_data": {
    "document_category": "DISCHARGE_SUMMARY",
    "patient_details": {
      "name": null,
      "age": null,
      "gender": null,
      "patient_id": null
    },
    "admission_details": {
      "admission_date": null,
      "discharge_date": null,
      "length_of_stay": null
    },
    "clinical_details": {
      "chief_complaint": null,
      "diagnosis": [],
      "procedures": [],
      "hospital_course": null
    },
    "vitals_at_discharge": {
      "blood_pressure": null,
      "pulse": null,
      "temperature": null,
      "respiratory_rate": null,
      "other": null
    },
    "medications_at_discharge": [],
    "follow_up": {
      "instructions": null,
      "next_visit_date": null,
      "referrals": []
    },
    "treating_team": {
      "primary_physician": null,
      "department": null,
      "hospital_name": null
    },
    "document_metadata": { "document_date": null },
    "document_summary": "",
    "source_filename": "Discharge_Summary.pdf"
  },
  "analysis": {
    "is_complete": false,
    "missing_critical_fields": [],
    "clinical_highlights": [],
    "medication_notes": [],
    "follow_up_actions": [],
    "patient_friendly_summary": ""
  }
}
```

- On OCR or structured-extraction failure, `structured_data` and `analysis` may both be `null`.
- **Requirements:** Same as insurance analyze — `GROQ_API_KEY`, Textract + S3 for PDFs.

---

### POST `/medilocker/prescription`

Extract structured prescription data from uploaded files using GPT-4 Vision.

**With files only:**

```json
{
  "files": [
    {
      "filename": "prescription.pdf",
      "content": "JVBERi0xLjQKJeLjz9MKMy...",
      "metadata": {}
    },
    {
      "filename": "lab_report.jpg",
      "content": "/9j/4AAQSkZJRgABAQAAAQ...",
      "metadata": {}
    }
  ]
}
```

**With frontend patient details:**

```json
{
  "files": [
    {
      "filename": "prescription.pdf",
      "content": "JVBERi0xLjQKJeLjz9MKMy...",
      "metadata": {}
    }
  ],
  "frontend_patient_details": {
    "name": "John Doe",
    "age": "45",
    "dob": "1980-01-15",
    "sex": "Male",
    "weight": "75",
    "allergies": "Penicillin",
    "pregnancy_bf": ""
  }
}
```

**Note:** Files should contain base64-encoded content. Supported formats: PDF, images (JPG, PNG, GIF, WEBP), and text files.

---

## Hospital Raw Data (Medilocker Lambda)

Hospital raw data ingestion endpoints. No OCR, Textract, or GPT — storage only. All endpoints require `x-hospital-api-key` header.

**Headers (required):**

```
x-hospital-api-key: YOUR_HOSPITAL_API_KEY
```

---

### POST `/hospital/upload`

Direct API upload: receive file as multipart/form-data, store in S3 and DynamoDB.

**Content-Type:** `multipart/form-data`

**Form Fields:**

- `hospital_id` (required)
- `patient_id` (required)
- `file` (required) — the file to upload

**Example (curl):**

```bash
curl -X POST "API/hospital/upload" \
  -H "x-hospital-api-key: YOUR_KEY" \
  -F "hospital_id=HOSP_001" \
  -F "patient_id=PAT_001" \
  -F "file=@lab_report.pdf"
```

**Response:**

```json
{
  "file_id": "a1b2c3d4",
  "message": "File uploaded successfully"
}
```

**Note:** Filenames with non-ASCII characters (e.g., narrow no-break spaces) are automatically sanitized for S3 metadata. Supported extensions: jpg, jpeg, png, heic, heif, webp, tiff, tif, bmp, pdf, doc, docx, xls, xlsx, csv, txt.

---

### POST `/hospital/presign-upload`

Generate presigned PUT URLs for direct S3 upload (supports multiple files). Hospital must call `POST /hospital/confirm-upload` after uploads complete.

```json
{
  "hospital_id": "HOSP_001",
  "patient_id": "PAT_001",
  "files": [{ "filename": "lab_report.pdf" }, { "filename": "scan.pdf" }]
}
```

**Single file:**

```json
{
  "hospital_id": "HOSP_001",
  "patient_id": "PAT_001",
  "files": [{ "filename": "lab_report.pdf" }]
}
```

**Response:**

```json
{
  "uploads": [
    {
      "file_id": "a1b2c3d4",
      "filename": "lab_report.pdf",
      "upload_url": "https://s3.amazonaws.com/..."
    },
    {
      "file_id": "e5f6g7h8",
      "filename": "scan.pdf",
      "upload_url": "https://s3.amazonaws.com/..."
    }
  ]
}
```

**Note:** At least one file is required. Use `file_id` from each response item when calling `confirm-upload`.

---

### POST `/hospital/confirm-upload`

Confirm presigned uploads completed. Saves metadata to DynamoDB for each file. Call after successfully uploading files to the presigned URLs.

```json
{
  "hospital_id": "HOSP_001",
  "patient_id": "PAT_001",
  "files": [
    {
      "file_id": "a1b2c3d4",
      "filename": "lab_report.pdf",
      "file_size": 102400
    },
    {
      "file_id": "e5f6g7h8",
      "filename": "scan.pdf",
      "file_size": 204800
    }
  ]
}
```

**Single file:**

```json
{
  "hospital_id": "HOSP_001",
  "patient_id": "PAT_001",
  "files": [
    {
      "file_id": "a1b2c3d4",
      "filename": "lab_report.pdf",
      "file_size": 102400
    }
  ]
}
```

**Response:**

```json
{
  "confirmed": [
    { "file_id": "a1b2c3d4", "message": "Upload confirmed, metadata saved" },
    { "file_id": "e5f6g7h8", "message": "Upload confirmed, metadata saved" }
  ],
  "errors": []
}
```

---
