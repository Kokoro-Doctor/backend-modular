# OCR Implementation — Repository Audit

This document describes how **Optical Character Recognition (OCR)** is implemented in this codebase: AWS Textract usage, S3 interactions, synchronous vs asynchronous invocation, and related services.

For a shorter Textract API summary, see [AWS_TEXTRACT_USAGE.md](./AWS_TEXTRACT_USAGE.md).

---

## Overview

**AWS Textract** is used only in the **Medilocker Lambda** Python service. A single module owns the boto3 client and all Textract calls: `backend/medilocker_lambda/app/services/ocr_service.py`. There is **no** Textract usage in the frontend or other lambdas.

| Area | Role |
|------|------|
| **OCR implementation** | `ocr_service.py` — `detect_document_text` (images, bytes), `start_document_analysis` + `get_document_analysis` (PDFs via S3 object reference) |
| **Consumers** | `file_service`, `extraction_service`, `insurance_extraction_service`, `discharge_extraction_service`, `claim_validator_graph` |
| **API surface** | `medilocker_router.py` — upload, prescription extraction, insurance analyze, discharge analyze |
| **Persistence** | Upload path writes `ocr.txt` to S3 and updates DynamoDB; prescription-from-library uses **structured_data** in DynamoDB, not raw `ocr.txt` |
| **Queues / S3 triggers** | **None** for OCR |
| **SNS** | Only **SMS** in `auth_lambda` — unrelated to OCR |

**Important naming correction:** Several docstrings say “`analyze_document`”; the code does **not** call the synchronous `AnalyzeDocument` API. PDFs use **asynchronous** Textract jobs: **`StartDocumentAnalysis`** and **`GetDocumentAnalysis`** (with in-process polling).

---

## OCR Flow Diagram (text-based)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Path A: POST /medilocker/upload (images only per ALLOWED_EXTENSIONS)         │
└─────────────────────────────────────────────────────────────────────────────┘
  Client → API (base64 files in body)
    → Decode bytes → S3 PutObject (original.{ext})
    → DynamoDB create (ocr_status=PENDING)
    → extract_text_from_image(bytes)  [Textract DetectDocumentText, sync]
    → S3 PutObject (ocr.txt)
    → DynamoDB ocr_status=COMPLETED
    → Groq structured extraction → DynamoDB structured_data
  ← Response after full pipeline completes (blocking)

┌─────────────────────────────────────────────────────────────────────────────┐
│ Path B: POST /medilocker/prescription (direct files, base64)               │
└─────────────────────────────────────────────────────────────────────────────┘
  Client → Parallel ThreadPool: per file extract_text_from_image(bytes)
    → Parallel Groq extraction → synthesis
  ← JSON response (blocking)

┌─────────────────────────────────────────────────────────────────────────────┐
│ Path C: POST /medilocker/insurance/analyze (+ claim graph)                   │
│         POST /medilocker/discharge/analyze                                    │
│         insurance_extraction_service / claim_validator_graph (PDF branch)     │
└─────────────────────────────────────────────────────────────────────────────┘
  Client → Multipart bytes in memory
    IF image: extract_text_from_image(bytes)  [sync]
    IF PDF:   S3 PutObject temp key …/_temp/.../document.pdf
              → start_document_analysis(S3Object) → poll get_document_analysis
              → S3 DeleteObject temp (best effort)
    → LLM steps…
  ← JSON (blocking)

┌─────────────────────────────────────────────────────────────────────────────┐
│ Path D: POST /medilocker/users/{id}/prescription                             │
└─────────────────────────────────────────────────────────────────────────────┘
  Client → Load latest docs from DynamoDB (ocr + structured COMPLETED)
    → build_patient_context from structured_data only (no Textract)
  ← Prescription (blocking)
```

---

## Sync vs Async Analysis

| Layer | Behavior |
|--------|----------|
| **HTTP API** | **Synchronous** — Lambda handles the request until OCR + downstream steps finish (or fail). |
| **Textract – images** | **Synchronous** API: `DetectDocumentText` with `Document={"Bytes": …}`. |
| **Textract – PDFs** | **Asynchronous job API** (`StartDocumentAnalysis` / `GetDocumentAnalysis`), but the **caller blocks**: poll every **3s**, up to **120s**, in the same Lambda invocation. Not SQS/SNS completion; no separate worker. |
| **Background queues** | **No** SQS/EventBridge/Lambda-on-S3 for OCR in this repo. |

---

## File Handling Strategy

| Scenario | Before OCR | Textract input | After OCR |
|----------|------------|----------------|------------|
| Medilocker upload | Original to S3; OCR uses **in-memory** bytes (same bytes as uploaded) | **Bytes** (`DetectDocumentText`) | `ocr.txt` on S3; metadata in DynamoDB |
| Prescription from files | Base64 in JSON | **Bytes** | In-memory only for pipeline |
| Insurance / discharge PDF | **Temp** object: `{S3_FOLDER_PREFIX}_temp/insurance|discharge/{id}/document.{ext}` | **S3Object** (`StartDocumentAnalysis`) | Temp object **deleted** after Textract |
| Insurance graph (PDF) | Same temp pattern as insurance service | **S3Object** | Temp deleted |
| Prescription from saved docs | N/A | **No Textract** | Uses stored **structured** JSON |

`ALLOWED_EXTENSIONS` for Medilocker upload and prescription-from-files is **image-only** (no PDF): `jpg`, `jpeg`, `png`, `heic`, `heif`, `webp`, `tiff`, `tif`, `bmp`. PDF OCR exists only on insurance/discharge/analyze paths (and monolithic insurance helper).

---

## Services Used

**Textract operations actually invoked** (from `ocr_service.py`):

1. **`DetectDocumentText`** — `Document={"Bytes": image_bytes}`; blocks until response.
2. **`StartDocumentAnalysis`** — `DocumentLocation.S3Object`, `FeatureTypes=["FORMS", "TABLES"]`.
3. **`GetDocumentAnalysis`** — status polling and paginated block reads (`NextToken`).

**Not used in code:** synchronous **`AnalyzeDocument`**, **`StartDocumentTextDetection`**, **`GetDocumentTextDetection`** (though IAM in `template.yaml` allows some of these for future/overage).

**S3:** `put_object` (originals, `ocr.txt`, temp PDFs), `delete_object` (temp cleanup), batch delete on document removal.

**DynamoDB:** `ocr_status`, `s3_ocr_key`, `structured_status`, `structured_data` on `MedilockerDocuments`.

**IAM** (`backend/template.yaml`): grants `textract:AnalyzeDocument`, `DetectDocumentText`, `StartDocumentAnalysis`, `GetDocumentAnalysis`, `StartDocumentTextDetection`, `GetDocumentTextDetection` — broader than current code.

---

## Observations / Issues

1. **Doc vs code:** Comments refer to **`analyze_document`**; implementation is **`StartDocumentAnalysis` / `GetDocumentAnalysis`** — can confuse audits and cost reviews.
2. **PDF feature choice:** PDF path requests **FORMS** and **TABLES**, but aggregation code only keeps **`LINE`** blocks — likely paying for analysis tiers without using structured outputs; `StartDocumentTextDetection` may fit if only line OCR is needed.
3. **No job notification path:** Async Textract is **poll-only**; long PDFs risk **timeout** (120s cap in code vs Lambda timeout in `template.yaml`).
4. **`extract_text_from_image`** raises **`HTTPException`** on Textract errors; **`file_service._run_and_store_ocr`** catches generic `Exception` — behavior is OK for uploads (mark FAILED), but call sites differ in error typing.
5. **Prescription-from-library** uses **`structured_data` only** (`context_service.build_patient_context`); **`ocr.txt`** is stored but not read on that path. Router/module comments that say OCR is “fetched from S3” for prescription are **misleading** for the current implementation.
6. **Medilocker upload** never uses **`extract_text_from_pdf_s3`** — PDFs are **not** in upload allowed extensions, so upload OCR is image-only.
7. **Retries:** No retry/backoff for throttling on Textract; only the polling loop for async jobs.
8. **SNS in repo** is for **SMS** (`auth_lambda`), not OCR pipelines.

---

## Suggestions for Improvement

1. Align **IAM** with **actual** API calls; add **`StartDocumentTextDetection` / `GetDocumentTextDetection`** only if you switch PDF OCR to that model.
2. If only plain text is required from PDFs, switch to **DetectDocumentText**-style pricing via **async text detection** and drop **FORMS/TABLES** unless you parse those block types.
3. Increase **observability**: metrics/alerts on Textract **`ThrottlingException`**, job **`FAILED`**, and **poll timeout** rate.
4. For large PDFs: **longer Lambda timeout**, **longer `_TEXTRACT_MAX_WAIT`**, or a **true async** pattern (SNS completion + separate worker, or Step Functions).
5. Fix **documentation strings** (`insurance_extraction_service`, `discharge_extraction_service`, `medilocker_router` prescription comment) to match **bytes vs S3** and **structured_data vs ocr.txt**.
6. Optional: **read `ocr.txt`** from S3 as fallback when **`structured_data`** failed but OCR succeeded, if product needs that resilience.

---

## File / function index (OCR-related)

| File | Functions / notes |
|------|-------------------|
| `backend/medilocker_lambda/app/services/ocr_service.py` | `extract_text_from_image`, `extract_text_from_pdf_s3` |
| `backend/medilocker_lambda/app/services/file_service.py` | `upload_files`, `_run_and_store_ocr` |
| `backend/medilocker_lambda/app/services/extraction_service.py` | `_extract_ocr_from_file`, `extract_structured_data_from_files` |
| `backend/medilocker_lambda/app/services/insurance_extraction_service.py` | `_upload_temp_to_s3`, `_cleanup_temp_s3`, `_extract_ocr`, `extract_insurance_data_from_file` |
| `backend/medilocker_lambda/app/services/discharge_extraction_service.py` | Same pattern as insurance for `_extract_ocr` / temp S3 |
| `backend/medilocker_lambda/app/services/claim_validator_graph.py` | `_upload_temp_to_s3`, `_cleanup_temp_s3`, `ocr_extractor` |
| `backend/medilocker_lambda/app/routers/medilocker_router.py` | Routes: `/upload`, `/prescription`, `/insurance/analyze`, `/discharge/analyze`, etc. |

---

*Generated from a full repository scan; keep in sync when OCR or Textract usage changes.*
