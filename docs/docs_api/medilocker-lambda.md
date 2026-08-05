# Medilocker Lambda

### POST `/medilocker/upload`

Upload medical files to user's medilocker. **`multipart/form-data`** — files
are sent as raw bytes, not base64. (Previously a base64-in-JSON body; base64
inflates payload size ~33%, which combined with API Gateway's hard 10 MB
request limit could reject files under the documented 10 MB max before the
Lambda ever ran. Multipart removes that overhead.)

**Request:** `multipart/form-data`

| Field      | Type          | Required | Description                                                                                    |
| ---------- | ------------- | -------- | ------------------------------------------------------------------------------------------------ |
| `user_id`  | string (Form) | Yes      | Medilocker owner                                                                                  |
| `files`    | file[]        | Yes      | Repeated `files` field, one part per file                                                         |
| `metadata` | string (Form) | No       | JSON object keyed by filename, e.g. `{"lab_report.jpg": {"file_type": "lab_report", "date": "2025-01-10"}}` |

**cURL example (multiple files):**

```bash
curl -X POST "https://<API_BASE>/medilocker/upload" \
  -F "user_id=USR_12345678-1234-1234-1234-123456789012" \
  -F "files=@prescription.pdf" \
  -F "files=@lab_report.jpg" \
  -F 'metadata={"prescription.pdf":{"type":"prescription","date":"2025-01-15"},"lab_report.jpg":{"type":"lab_report","date":"2025-01-10"}}'
```

**Single file:**

```bash
curl -X POST "https://<API_BASE>/medilocker/upload" \
  -F "user_id=USR_12345678-1234-1234-1234-123456789012" \
  -F "files=@prescription.pdf"
```

`metadata` is optional — omit it entirely if you have no per-file metadata to attach. A filename with no matching key in `metadata` just gets `{}`.

---

### POST `/medilocker/upload/async`

Same request shape as `POST /medilocker/upload` (images + PDFs accepted,
`multipart/form-data`). Stores the file in S3, writes a `MedilockerDocuments`
record with `ocr_status=PENDING, upload_mode=ASYNC`, then dispatches an SQS
message to `OCRWorkerLambda` and returns **202** immediately instead of
waiting for OCR. Poll `GET /medilocker/users/{user_id}/files/{file_id}/status`
to track completion.

**Request:** `multipart/form-data` — same `user_id` / `files` / `metadata` fields as `POST /medilocker/upload`.

```bash
curl -X POST "https://<API_BASE>/medilocker/upload/async" \
  -F "user_id=USR_12345678-1234-1234-1234-123456789012" \
  -F "files=@lab_report.pdf" \
  -F 'metadata={"lab_report.pdf":{"file_type":"scan_report"}}'
```

**Response (202 Accepted):**

```json
{
  "message": "Files accepted for background processing",
  "files": [
    { "file_id": "a1b2c3d4", "filename": "lab_report.pdf", "status": "PENDING" }
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

### GET `/medilocker/users/{user_id}/files/{file_id}/status`

Poll OCR / structured-extraction progress for a single file. Used by the
async upload flow (`POST /medilocker/upload/async`) to find out when
processing finishes; also works for sync uploads.

**Path Parameters:**

- `user_id`: User ID (required)
- `file_id`: File ID returned from upload (required)

**Response:**

```json
{
  "file_id": "a1b2c3d4",
  "filename": "lab_report.pdf",
  "ocr_status": "COMPLETED",
  "structured_status": "COMPLETED",
  "upload_mode": "ASYNC",
  "document_category": "LAB_REPORT",
  "updated_at": "2026-06-20T10:15:00+00:00"
}
```

`ocr_status` / `structured_status`: `PENDING` \| `COMPLETED` \| `FAILED` \| `SKIPPED`. `upload_mode`: `LIVE` \| `ASYNC`. **404** if `file_id` doesn't exist for the given `user_id`.

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

Save an approved prescription to the patient's Medilocker. Stores the prescription as a document that appears in file listings and can be downloaded via the existing download endpoint. Document category is set to `PRESCRIPTION`. **`multipart/form-data`** — the PDF is sent as raw bytes, not base64.

**Path Parameters:**

- `user_id`: Patient's user ID (Medilocker owner)

**Request:** `multipart/form-data`

| Field      | Type          | Required | Description                                                         |
| ---------- | ------------- | -------- | --------------------------------------------------------------------- |
| `file`     | file          | Yes      | Prescription PDF                                                      |
| `filename` | string (Form) | No       | Display filename override; defaults to the uploaded file's filename, or `Prescription_{date}_{time}.pdf` if that's also empty |

**Example:**

```bash
curl -X POST "https://<API_BASE>/medilocker/users/USR_12345678-1234-1234-1234-123456789012/prescription/save" \
  -F "file=@Rx.pdf"
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

### POST `/medilocker/insurance/analyze/stream`

Same input as `POST /medilocker/insurance/analyze` (single file,
`multipart/form-data`), but streams progress over **Server-Sent Events**
instead of waiting for the full pipeline to finish — useful for showing
live chain-of-thought / node-by-node progress in the UI.

**Request:** `multipart/form-data`

| Field  | Type | Required | Description                          |
| ------ | ---- | -------- | ------------------------------------- |
| `file` | file | Yes      | Insurance document (image or PDF)    |

**Response:** `text/event-stream`. Each SSE event's `event` name is the
pipeline node that just completed (or `"update"`); `data` is a JSON payload
for that node. The stream ends when the claim-validation graph finishes.

```bash
curl -N -X POST "https://<API_BASE>/medilocker/insurance/analyze/stream" \
  -F "file=@/path/to/Claim_Form.pdf"
```

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

## Hospital Raw Data (REMOVED)

`POST /hospital/upload`, `POST /hospital/presign-upload`, and `POST /hospital/confirm-upload`
(API-key auth, storage-only into a separate `HospitalFiles` table) have been
**removed** from this Lambda. Hospitals now upload patient documents through
the JWT-secured staff endpoints in [hospitals-lambda.md](hospitals-lambda.md)
(`POST /hospitals/staff/add-patient`, `POST /hospitals/staff/update_patient`),
which write into the same `MedilockerDocuments` table as patient self-uploads
(tagged `source=HOSPITAL`). See `docs/DOCUMENT_UPLOAD_AND_ACCESS.md` for the
full model.

---
