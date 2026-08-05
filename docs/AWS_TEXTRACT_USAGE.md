# AWS Textract usage in this codebase

This document summarizes where Textract is used, which APIs are invoked, and practical improvements.

## Where Textract lives

| Location | Role |
|----------|------|
| `backend/medilocker_lambda/app/services/ocr_service.py` | **Only** module that creates a Textract client and calls Textract APIs |
| `insurance_extraction_service.py`, `discharge_extraction_service.py`, `claim_validator_graph.py`, `extraction_service.py`, `file_service.py` | Import `extract_text_from_image` / `extract_text_from_pdf_s3` only |
| `backend/template.yaml` | IAM permissions for Textract (+ extra actions not used in code) |

There is **no** Node/TypeScript AWS SDK Textract usage in the frontend or other services (no `@aws-sdk/client-textract`).

---

## Client initialization

| File | Line | Code |
|------|------|------|
| `ocr_service.py` | 14 | `textract_client = boto3.client("textract", region_name=AWS_REGION)` |

---

## API calls (by operation)

### 1. DetectDocumentText (basic OCR)

| File | Line(s) | Call | API type |
|------|---------|------|----------|
| `ocr_service.py` | 32–34 | `textract_client.detect_document_text(Document={"Bytes": image_bytes})` | **DetectDocumentText** |

**Used for:** In-memory images (bytes). Output is flattened from `BlockType == "LINE"` blocks.

### 2. StartDocumentAnalysis / GetDocumentAnalysis (async, forms + tables)

| File | Line(s) | Call | API type |
|------|---------|------|----------|
| `ocr_service.py` | 96–104 | `textract_client.start_document_analysis(DocumentLocation={...}, FeatureTypes=["FORMS", "TABLES"])` | **StartDocumentAnalysis** |
| `ocr_service.py` | 113 | `textract_client.get_document_analysis(JobId=job_id)` | **GetDocumentAnalysis** (status polling) |
| `ocr_service.py` | 129 | `textract_client.get_document_analysis(**kwargs)` | **GetDocumentAnalysis** (pagination) |

**Used for:** Multi-page PDFs in S3. The code still only **reads LINE blocks** to build plain text; it does not use key-value pairs or table geometry.

---

## APIs not used in application code

From the common Textract surface area:

| API | In code? |
|-----|----------|
| AnalyzeDocument (sync) | **No** — comments in `insurance_extraction_service.py` / `discharge_extraction_service.py` mention “analyze_document,” but implementation is **async** `StartDocumentAnalysis` / `GetDocumentAnalysis`. |
| AnalyzeExpense | No |
| AnalyzeID | No |
| StartDocumentTextDetection / GetDocumentTextDetection | **No** in Python — only allowed in `template.yaml` IAM |

---

## IAM (`backend/template.yaml`)

The Medilocker/Lambda role allows:

- `textract:AnalyzeDocument`
- `textract:DetectDocumentText`
- `textract:StartDocumentAnalysis`
- `textract:GetDocumentAnalysis`
- `textract:StartDocumentTextDetection`
- `textract:GetDocumentTextDetection`

**Actually invoked:** `DetectDocumentText`, `StartDocumentAnalysis`, `GetDocumentAnalysis`.

---

## Summary metrics

| Metric | Value |
|--------|--------|
| Distinct Textract **operations** in code | 3 (`detect_document_text`, `start_document_analysis`, `get_document_analysis`) |
| Static boto3 **call sites** | 4 (two for `get_document_analysis`: poll + pagination) |
| Highest call volume at runtime | **`get_document_analysis`** (poll loop: 3s interval, up to 120s timeout; plus pagination per job) |

### Cost category (by path)

| Path | Category | Notes |
|------|----------|--------|
| Images | **Cheap (OCR)** | `DetectDocumentText` pricing |
| PDFs | **Expensive (Forms/Analysis)** | `FeatureTypes=["FORMS", "TABLES"]` uses **Analyze Document**-class pricing even though only LINE text is consumed |

---

## Suggested improvements

### 1. Prefer async **text detection** for PDFs if you only need raw text

If the product only needs OCR lines (as today), consider **`StartDocumentTextDetection`** + **`GetDocumentTextDetection`** instead of `StartDocumentAnalysis` with `FORMS` and `TABLES`. That typically aligns with **lower** Textract pricing than full forms/tables analysis.

**Trade-offs:** Confirm AWS pricing for your region/features; validate output quality on your sample PDFs (tables may be less structured if you later need them).

### 2. Tighten IAM to least privilege

Remove unused actions from `template.yaml` (e.g. `AnalyzeDocument` if never called; `StartDocumentTextDetection` / `GetDocumentTextDetection` until you adopt them, or keep them only if you switch PDF OCR to text detection).

### 3. Fix misleading docstrings

Update module docstrings in `insurance_extraction_service.py` and `discharge_extraction_service.py` to say **StartDocumentAnalysis / GetDocumentAnalysis** (or “async document analysis”) instead of “analyze_document,” to match boto3 and avoid confusion during audits.

### 4. Optional: reduce `GetDocumentAnalysis` chatter

**Polling:** You could use exponential backoff with a cap (still bounded by max wait) to cut API calls during long jobs, or use SNS completion notifications if you refactor to an async Lambda pattern.

**Pagination:** Current pagination loop is appropriate; no change required unless pages grow very large.

### 5. Operational hardening (if not already present)

- **CloudWatch alarms** on Textract `ThrottlingException` / error rates.
- **Idempotency** and deduplication at the upload/OCR boundary if the same PDF can be submitted repeatedly.

### 6. When FORMS/TABLES is actually justified

If you later need structured fields or tables from PDFs, **then** `FeatureTypes=["FORMS", "TABLES"]` (or targeted **Queries**) is appropriate — and you should parse those block types instead of only LINEs.

---

## Revision history

- Initial audit documented usage in `ocr_service.py` and call graph from consuming services; improvement section added for cost, IAM, and documentation alignment.
