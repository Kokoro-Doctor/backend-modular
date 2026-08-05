# Prescription Generation

## Overview

Prescription generation converts uploaded medical documents into structured, consolidated prescription text. The system has two flows:

- **Flow A** — Stored documents (primary). All heavy lifting (OCR + extraction) happens once at upload time. Prescription generation only merges and synthesises.
- **Flow B** — Direct extraction (stateless). Client sends files directly; the full pipeline runs on the fly. Used by the doctor's portal for ad-hoc analysis without storing files.

---

## Flow A — Prescription from Stored Documents (Primary)

```
POST /medilocker/users/{user_id}/prescription
```

This is the primary flow. OCR and per-document extraction have **already run** at upload time and the results are stored in DynamoDB. Prescription generation does **zero** OCR and **zero** per-document GPT calls — it only merges and synthesises.

### What happens at upload time (synchronous)

When a file is uploaded, OCR and structured extraction run **synchronously** (inline) before the response is sent. Lambda freezes when the handler returns, so background threads would not complete. Two stages run in sequence:

```
Uploaded file
  │
  ▼
Stage A: OCR (Textract)
  └── Images → detect_document_text (bytes)
      (Images only — no PDF support)
  │
  ▼
Store OCR text → S3: {user_id}/{file_id}/ocr.txt
Update DynamoDB: ocr_status = COMPLETED
  │
  ▼
Stage B: GPT Structured Extraction
  Input:  OCR text
  Model:  gpt-4o
  Temp:   0.1 (factual, low creativity)
  Output: Strict JSON
  │
  ▼
Store in DynamoDB: structured_data (JSON string)
Update DynamoDB: structured_status = COMPLETED
```

**Extracted JSON schema:**
```json
{
  "document_category": "OTHER",
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

`document_category` is one of: `LAB_REPORT`, `SCAN_REPORT`, `HEALTH_INSURANCE`, `HOSPITAL_RECORD`, `OTHER`. It is stored in DynamoDB and used for filtering in the list-files endpoint.

If either stage fails, the corresponding status is set to `FAILED` and the document is excluded from prescription generation.

### What happens at prescription time

```
Request: POST /medilocker/users/{user_id}/prescription
  │
  ▼
Step 1: Query DynamoDB
  └── get_latest_documents(user_id, limit=PRESCRIPTION_MAX_DOCS)
      Ordered by created_at descending (newest first)
  │
  ▼
Step 2: Filter
  └── Keep only docs where ocr_status == COMPLETED
      AND structured_status == COMPLETED
  │
  ▼
Step 3: Parse
  └── JSON.parse each doc's structured_data string
      Skip docs with invalid/missing JSON
  │
  ▼
Step 4: Merge (Python, no GPT)
  ├── De-duplicate medications (by name + dose)
  ├── Prefer latest non-null patient details
  ├── Combine diagnoses, symptoms, labs, medical history, etc.
  └── Remove empty/null values
  │
  ▼
Step 5: GPT Synthesis (single call)
  Model:  gpt-4o
  Temp:   0.3 (natural text, slightly creative)
  Input:  Merged structured JSON
  Output: Final prescription text
  │
  ▼
Response
```

**GPT calls at prescription time:** exactly **1** (the final synthesis). No OCR. No per-document extraction.

### Response

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

`patient_details` is omitted entirely if all fields are null. `diagnosis` is the first item from the merged `diagnoses` list.

### Edge cases

| Condition | Result |
|-----------|--------|
| No documents for user | `{ "prescription": "" }` |
| Documents exist but none have `COMPLETED` status | `{ "prescription": "" }` |
| `structured_data` is corrupt JSON | Document skipped, others still used |
| All `structured_data` is corrupt | `{ "prescription": "" }` |

---

## Flow B — Direct Extraction (Stateless)

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

Files are **not** stored in Medilocker. This is a stateless pipeline that runs all 4 stages in a single request. Used by the doctor's portal for on-the-fly extraction.

### Pipeline

```
Client sends base64-encoded image files
  │
  ▼
Stage 1: Parallel OCR (Textract, ThreadPoolExecutor)
  └── Images → detect_document_text (bytes)
      (Images only — jpg, jpeg, png, heic, heif, webp, tiff, tif, bmp)
  Max workers: min(num_files, PRESCRIPTION_MAX_DOCS)
  │
  ▼
Stage 2: Parallel GPT Extraction (per document)
  Model:  gpt-4o,  Temp: 0.1
  Input:  OCR text per document
  Output: Structured JSON per document
  Max workers: min(num_docs, PRESCRIPTION_MAX_DOCS)
  │
  ▼
Stage 3: Merge (Python, no GPT)
  Same merge logic as Flow A
  │
  ▼
Stage 4: GPT Synthesis (single call)
  Model:  gpt-4o,  Temp: 0.3
  Input:  Merged structured JSON
  Output: Final prescription text
  │
  ▼
Response: { prescription, patient_details? }
```

**GPT calls:** 1 per document (extraction) + 1 (synthesis) = N + 1 total.

### Logging trace

Each stage logs its output (truncated for readability):

| Log prefix | Stage | Content logged |
|------------|-------|----------------|
| `[PRESCRIPTION] Stage 1 (OCR)` | OCR | Per document: filename, char count, text preview |
| `[PRESCRIPTION] Stage 2 (GPT extract)` | GPT extract | Per document: extracted JSON (truncated) |
| `[PRESCRIPTION] Stage 3 (merged data)` | Merge | Merged JSON (truncated) |
| `[PRESCRIPTION] Stage 4 (final prescription)` | Synthesis | Prescription length + text preview |

The direct extraction endpoint uses `[EXTRACT]` prefix for request/completion logs.

---

## Synthesis Prompt

The final GPT synthesis call uses this structure:

**Sections generated (non-empty only):**
1. **Clinical Summary** (mandatory, max 4–5 lines)
   - Highlights abnormal/borderline findings
   - Does not exaggerate risk or invent diagnoses
2. **Diagnosis** (only if clearly supported)
3. **Key Findings**
4. **Medications**
5. **Advice / Lifestyle Recommendations**
6. **Follow-up Recommendations**

**Rules enforced by prompt:**
- No duplicate information across sections
- No hallucinated medical conditions
- Use ONLY the provided structured data
- Omit empty sections entirely

**Response format:** The synthesis returns JSON with a single `prescription` key containing the full formatted text (using `\n` for line breaks).

---

## Context Building

Implemented in `context_service.build_patient_context()`. Processes documents in chronological order (newest first):

| Data type | Strategy |
|-----------|----------|
| `patient_summary` | Latest non-null value per field (name, age, gender) from any document |
| `document_history` | Full list of documents with `structured_data`, sorted by `created_at` descending |
| Per-document data | Each document's `structured_data` (diagnoses, medications, lab_values, etc.) is preserved in `document_history` |

The GPT synthesis prompt receives the full `patient_summary` and `document_history`; the model handles consolidation and de-duplication in its output. `document_category` is used for filtering (e.g. list-files by category).

---

## Service Functions

| Function | Called by | Purpose |
|----------|-----------|---------|
| `extract_structured_data_for_document(ocr_text)` | file_service (upload) | Runs GPT extraction on one document's OCR text |
| `generate_prescription_from_context(patient_context)` | Router (Flow A), extraction_service (Flow B) | Final GPT synthesis from patient context |
| `build_patient_context(documents)` | Router (Flow A) | Merges structured_data from DynamoDB docs into patient context |
| `extract_structured_data_from_files(files)` | Router (Flow B) | Full pipeline: OCR → extract → context → synthesis |
| `extract_structured_data_from_ocr_texts(texts)` | Legacy (v2) | Extract → context → synthesis from pre-computed OCR texts |
| `_extract_structured_data_from_text(text, client)` | Internal | Single-document GPT extraction |
| `_generate_prescription_from_context(context, client)` | prescription_service | Internal: final GPT synthesis call |

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | Required. OpenAI API key |
| `PRESCRIPTION_MAX_DOCS` | `10` | Max documents to include in prescription |

---

## Performance Comparison

| | Flow A (stored) | Flow B (direct) |
|---|---|---|
| OCR calls | 0 (done at upload) | N (parallel) |
| GPT extraction calls | 0 (done at upload) | N (parallel) |
| GPT synthesis calls | 1 | 1 |
| Total GPT calls | **1** | **N + 1** |
| Typical latency | 2–5s | 15–60s (depends on N and file sizes) |
| Data source | DynamoDB `structured_data` | Base64 files from client |

Flow A is significantly faster and cheaper because all heavy processing happens once at upload time and is cached in DynamoDB.

---

## Frontend Integration

### Flow A — Doctor generates prescription from stored patient documents
```javascript
// GeneratePrescription.jsx
const response = await fetch(
  `${API_URL}/medilocker/users/${userId}/prescription`,
  { method: "POST", headers: { "Content-Type": "application/json" } }
);
const data = await response.json();
// data.prescription — formatted text
// data.patient_details — { name, age, gender, diagnosis }
```

### Flow B — Direct extraction from uploaded files
```javascript
// Uses extractStructuredData() from MedilockerService.js
const data = await extractStructuredData(files);
// files = [{ filename: "scan.jpg", content: "<base64>" }]
```

---

## Error Handling

| Error | Behavior |
|-------|----------|
| `OPENAI_API_KEY` not set | 500 — "OpenAI API key not configured" |
| No files / no documents | Returns `{ "prescription": "" }` |
| OCR fails for one file | Skipped, other files still processed |
| GPT extraction fails for one doc | Skipped, other docs still processed |
| All extractions fail | Returns `{ "prescription": "" }` |
| GPT synthesis fails | 500 — error propagated |
| Invalid `structured_data` JSON in DynamoDB | Document skipped with warning log |

---

## Related Docs

| Doc | Contents |
|-----|----------|
| `CLINICAL_QUERY.md` | Clinical Q&A endpoint — doctor questions over patient records (separate from prescription) |
| `CLINICAL_QUERY_HISTORY.md` | Design for clinical query follow-up history |
| `MEDILOCKER_FLOW.md` | Complete Medilocker system documentation |
