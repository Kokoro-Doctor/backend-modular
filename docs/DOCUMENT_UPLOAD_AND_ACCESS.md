# Document upload & access — unified model

How patient documents get into S3/DynamoDB, and who is allowed to see what.
Supersedes the old separate `MedilockerDocuments` / `HospitalFiles` split —
everything now lives in one table, tagged by source.

---

## The model

Every document belongs to **one patient** (`user_id`), no matter who uploaded
it. The only extra fact needed is **who added it**:

```
MedilockerDocuments
  user_id        (PK)   ← the patient, always
  created_at     (SK)
  file_id
  s3_original_key       ← Medilocker/Users/{user_id}/{file_id}/original.{ext}
  s3_ocr_key            ← Medilocker/Users/{user_id}/{file_id}/ocr.txt
  ocr_status, document_category, structured_status, ...
  source         "USER" | "HOSPITAL"
  hospital_id    <hid>   present ONLY when source = "HOSPITAL"
```

* **S3 layout is always patient-centric** — `Medilocker/Users/{user_id}/{file_id}/…` —
  even for hospital uploads. The OCR worker only knows this one prefix, so
  hospital docs flow through OCR unchanged.
* `hospital_id` is **absent** on user-uploaded docs. That makes the
  `hospital-index` GSI **sparse**: it contains only hospital-sourced rows, so
  it doubles as "every doc this hospital uploaded."

**Table:** `template.yaml` → `MedilockerDocumentsTable`
**Indexes:**
- `file_id-index` (PK `file_id`) — direct file lookup
- `hospital-index` (PK `hospital_id`, SK `created_at`) — hospital dashboard view

---

## Upload endpoints

| Method | Path | Lambda | Caller | Writes |
|---|---|---|---|---|
| POST | `/medilocker/upload` | medilocker | Patient app | `source=USER`, sync OCR |
| POST | `/medilocker/upload/async` | medilocker | Patient app | `source=USER`, async OCR via SQS |
| POST | `/medilocker/users/{user_id}/prescription/save` | medilocker | Patient app | `source=USER`, prescription PDF |
| POST | `/hospitals/staff/add-patient` | hospitals | Hospital staff (JWT) | `source=HOSPITAL`, `hospital_id` = JWT hospital, async OCR — fixed 3 admission docs |
| POST | `/hospitals/staff/update_patient` | hospitals | Hospital staff (JWT) | `source=HOSPITAL`, `hospital_id` = JWT hospital, async OCR — fixed 3 admission docs |
| POST | `/hospitals/staff/patients/{user_id}/documents` | hospitals | Hospital staff (JWT) | `source=HOSPITAL`, `hospital_id` = JWT hospital, async OCR — arbitrary file count, free-form `doc_type` (defaults `OTHER`), no patient-form fields required |

`hospital_id` on the staff-app routes is read from the **JWT** via
`get_current_hospital` / `assert_hospital_id_matches_token` — never trusted
from the request body. This is what makes the isolation guarantee hold.

`POST /hospitals/staff/patients/{user_id}/documents` is the hospital-side
equivalent of `POST /medilocker/upload` — use it to attach docs to a patient
that already exists, without re-submitting the full add/update-patient form.
It does **not** verify `user_id` is linked to the calling hospital (or exists
at all); the caller is responsible for passing a valid one.

### Implementation pointers
- `medilocker_lambda/app/services/file_service.py` — user uploads
- `hospitals_lambda/app/services/patient_doc_service.py` — staff-app uploads (`_process_single_doc`, `upload_documents_for_patient`)
- `medilocker_lambda/app/services/document_db_service.py` — `create_document_record` (writes `source`/`hospital_id`)

---

## List / read endpoints

| Method | Path | Lambda | Returns |
|---|---|---|---|
| GET | `/medilocker/users/{user_id}/files` | medilocker | All docs for the patient — self uploads + every hospital's uploads |
| GET | `/medilocker/users/{user_id}/files/{file_id}/download` | medilocker | Presigned download URL |
| GET | `/medilocker/users/{user_id}/files/{file_id}/status` | medilocker | OCR / structured-extraction status |
| GET | `/hospitals/staff/patients/{user_id}/documents` | hospitals | This hospital's scoped view of one patient (see below) |
| GET | `/hospitals/staff/documents` | hospitals | This hospital's dashboard — every doc it uploaded, across all patients (see below) |

### Access rules

1. **Patient view** (`GET /medilocker/users/{user_id}/files`)
   Plain partition query — `user_id = :uid`. Already returns everything,
   regardless of `source`, because every document is keyed by the patient.

2. **Hospital-on-patient view** (`GET /hospitals/staff/patients/{user_id}/documents`)
   Partition query on `user_id`, filtered server-side to:
   ```
   source = "USER"  OR  hospital_id = :this_hospital
   ```
   `:this_hospital` comes from the caller's JWT. A hospital can never see
   another hospital's rows for the same patient — they're filtered out before
   the response is built.
   Implementation: `hospitals_lambda/app/services/patient_doc_service.py::list_patient_docs_for_hospital`.

3. **Hospital dashboard** (`GET /hospitals/staff/documents`)
   Query the sparse `hospital-index` GSI with `hospital_id = :hid`. Returns
   only `source=HOSPITAL` docs for this hospital, across every patient —
   patient self-uploads and other hospitals' docs never appear (the index is
   sparse precisely because user docs omit `hospital_id`).
   Implementation: `hospitals_lambda/app/services/patient_doc_service.py::list_documents_for_hospital`.

---

## What this replaced

The old `/hospital/upload`, `/hospital/presign-upload`, `/hospital/confirm-upload`
endpoints (medilocker_lambda `hospital_router.py`/`hospital_service.py`) wrote
to a **separate** `HospitalFiles` table under a `HospitalData/` S3 prefix, with
no OCR and no link back to the patient's Medilocker view. That code path has
been **deleted**. The `HospitalFiles` table and `HospitalData/`/`hospital_uploads/`
S3 objects still exist for historical data but receive no new writes — see
`docs/DATABASE_TABLES.md` for the frozen schema.

All hospital-side document ingestion now goes through the staff-app routes
above, which were already JWT-secured and patient-scoped.
