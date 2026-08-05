# Prescription & Case Analysis — Backend Improvements

Backend-only task list for the prescription and full case analysis flow. Pick items for March.

---

## New Endpoints & Features

| Task | Effort | Description |
|------|--------|-------------|
| **Patient-query endpoint** | Low | `POST /medilocker/users/{user_id}/patient-query` — Same as clinical-query but with patient-friendly GPT prompt (simple language, "What do my lab results mean?" style). |
| **Document selection for prescription** | Low | Extend `POST /users/{user_id}/prescription` with optional body `{ "file_ids": ["abc", "def"] }`. If provided, use only those docs; else keep current behavior (latest N). |
| **Prescription versioning** | Medium | New DynamoDB table `Prescriptions`. On generate, store `user_id`, `doctor_id`, `created_at`, `prescription_text`, `document_ids`. Add `GET /users/{user_id}/prescriptions` (paginated). |
| **Clinical query history** | Low | New DynamoDB table `ClinicalQueryHistory`. Store `user_id`, `doctor_id`, `question`, `answer`, `created_at` on each query. Optional: `GET /users/{user_id}/clinical-query-history`. |
| **Multi-language prescription** | Low | Add optional `language` param to prescription endpoint (e.g. `hi`, `en`). Adjust GPT synthesis prompt to output in that language. |

---

## Data & Extraction

| Task | Effort | Description |
|------|--------|-------------|
| **Confidence scores** | Medium | Store OCR/extraction confidence per document in DynamoDB. Surface in list-files or prescription response for low-confidence items. |
| **Document date ordering** | Low | Use `document_metadata.document_date` for chronological ordering when building patient context (fallback to `created_at` if null). |
| **Trend detection in synthesis** | Low | Update prescription synthesis prompt to explicitly detect and mention lab trends (e.g. HbA1c over time, lipid changes). |

---

## Performance & Scalability

| Task | Effort | Description |
|------|--------|-------------|
| **Async upload processing** | High | Move OCR + extraction to SQS + worker Lambda. Return 202 with `processing` status. Add poll endpoint `GET /users/{user_id}/files/{file_id}/status` or webhook. |
| **Presigned upload** | Medium | Add `POST /medilocker/upload-url` — return presigned S3 URL for direct client upload. Reduces Lambda payload and timeout risk. |
| **Pagination for list-files** | Low | Add `limit` and `last_file_id` (or `last_created_at`) query params to `GET /users/{user_id}/files`. |

---

## Security & Compliance

| Task | Effort | Description |
|------|--------|-------------|
| **Audit trail** | Medium | Log document access (fetch, download, prescription, clinical-query) to DynamoDB or CloudWatch. Include `user_id`, `actor_id`, `action`, `timestamp`. |
| **Rate limiting** | Low | Throttle prescription and clinical-query endpoints per user/doctor (e.g. API Gateway usage plan or Lambda-level counter). |
| **S3 encryption at rest** | Low | Enable SSE-S3 or SSE-KMS on the Medilocker S3 bucket. |

---

## Caching & Optimization

| Task | Effort | Description |
|------|--------|-------------|
| **Patient context cache** | Low | Cache `build_patient_context()` result for a short TTL (e.g. 5 min) when doctor generates multiple prescriptions or queries in a session. Key: `user_id`. |

---

## Quick Pick for March

**Low effort, high impact:**
- Patient-query endpoint
- Document selection for prescription
- Clinical query history
- Multi-language prescription
- Pagination for list-files
- Rate limiting

**Medium effort:**
- Prescription versioning
- Presigned upload
- Audit trail
