# Hospitals Lambda

Two routers are registered:
- **`/hospitals`** — CRUD, auth, relation management, and read-only views
- **`/hospitals/staff`** — hospital-staff operations (add/update patients & doctors, bulk Excel import); every request requires `Authorization: Bearer <hospital-token>` from `POST /hospitals/login` and the `hospital_id` in the body/form must match the JWT `sub`

CORS is allowed from `https://kokoro.doctor` and `http://localhost:8081`.

---

## Auth

### POST `/hospitals/signup`

Register a new hospital. Returns `201`. At least one of `email` or `contact_number` is required.

**Body:**

```json
{
  "name": "City General Hospital",
  "password": "SecurePassword123",
  "email": "contact@hospital.com",
  "contact_number": "+919587733170",
  "address": "123 Main Street",
  "city": "Mumbai",
  "state": "Maharashtra"
}
```

Required: `name`, `password`, and at least one of `email` / `contact_number`. Optional: `address`, `city`, `state`.

`hospital_id` is auto-generated as `HOSP_<uuid>`. Password is bcrypt-hashed; the plain-text value is never returned.

Duplicate `email` or `contact_number` returns `409 Conflict`.

**Response:**

```json
{
  "hospital": {
    "hospital_id": "HOSP_xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "name": "City General Hospital",
    "email": "contact@hospital.com",
    "contact_number": "+919587733170",
    "is_active": true,
    "created_at": "2026-05-13T10:00:00+00:00"
  },
  "token": "eyJ...",
  "expires_in": 28800,
  "message": "Hospital registered successfully"
}
```

A JWT is issued immediately so the hospital is authenticated without a separate login call. `password_hash` is never returned.

---

### POST `/hospitals/login`

Authenticate using email or mobile number and password.

**Body:**

```json
{
  "identifier": "contact@hospital.com",
  "password": "SecurePassword123"
}
```

`identifier` can be an email address or a mobile number; the server detects which by checking for `@`.

**Response:**

```json
{
  "hospital": { "hospital_id": "HOSP_xxx", "name": "...", "...": "..." },
  "token": "eyJ...",
  "expires_in": 28800,
  "message": "Login successful"
}
```

`expires_in` is in seconds; default expiry is 8 hours.

---

## Hospital CRUD

### GET `/hospitals/list`

List all **active** hospitals. No authentication required. No request body.

**Response:**

```json
{
  "hospitals": [{ "hospital_id": "HOSP_...", "name": "...", "...": "..." }]
}
```

---

### GET `/hospitals/get/{hospital_id}`

Get a single hospital by ID. The body `hospital_id` must match the path.

**Body:**

```json
{ "hospital_id": "HOSP_12345678-1234-1234-1234-123456789012" }
```

**Response:**

```json
{
  "hospital": { "hospital_id": "HOSP_xxx", "name": "...", "...": "..." }
}
```

---

### PUT `/hospitals/update/{hospital_id}`

Update hospital metadata. Body `hospital_id` must match the path. Only provided fields are updated.

**Body (only `hospital_id` required; all others optional):**

```json
{
  "hospital_id": "HOSP_12345678-1234-1234-1234-123456789012",
  "name": "Updated Hospital Name",
  "address": "456 New Street",
  "city": "Pune",
  "state": "Maharashtra",
  "contact_number": "+919999999999",
  "email": "new@hospital.com"
}
```

**Response:**

```json
{
  "hospital": { "hospital_id": "HOSP_xxx", "...": "..." }
}
```

---

### PUT `/hospitals/disable/{hospital_id}`

Soft-delete (deactivate) a hospital — sets `is_active = false`. Body `hospital_id` must match the path.

**Body:**

```json
{ "hospital_id": "HOSP_12345678-1234-1234-1234-123456789012" }
```

**Response:**

```json
{
  "hospital": { "hospital_id": "HOSP_xxx", "is_active": false, "...": "..." }
}
```

---

## Relations & Patient/Doctor Views

### POST `/hospitals/relations`

Create or update a user-doctor bond for a hospital. Returns `201`. Idempotent — re-posting the same pair refreshes metadata without creating a duplicate.

Requires `Authorization: Bearer <token>`. Body `hospital_id` must match JWT `sub`. Doctor must be affiliated with the hospital (checked via `DoctorHospital` junction).

**Body:**

```json
{
  "user_id": "usr_xxx",
  "doctor_id": "dr_xxx",
  "hospital_id": "HOSP_xxx",
  "relation_type": "HOSPITAL_ASSIGNED",
  "linked_by": "hospital_staff"
}
```

All five fields are required. `relation_type` defaults to `"HOSPITAL_ASSIGNED"`, `linked_by` defaults to `"hospital_staff"`.

**Response:**

```json
{
  "relation": {
    "user_id": "usr_xxx",
    "doctor_id": "dr_xxx",
    "hospital_id": "HOSP_xxx",
    "relation_type": "HOSPITAL_ASSIGNED",
    "status": "ACTIVE",
    "linked_by": "hospital_staff",
    "created_at": "2026-06-28T...",
    "updated_at": "2026-06-28T..."
  }
}
```

Note: `relation_id` is no longer present — the identity of the bond is the composite `(user_id, doctor_id)` key.

---

### GET `/hospitals/{hospital_id}/patients`

List all patients affiliated with this hospital.

Requires `Authorization: Bearer <token>`; path `hospital_id` must match JWT `sub`. Disabled hospitals return `403`.

**Response:**

```json
{
  "patients": [
    {
      "name": "Rahul Sharma",
      "phoneNumber": "+919999999999",
      "user_id": "usr_xxx",
      "gender": "Male",
      "age": 40,
      "createdAt": "2026-05-09T09:29:57.714378+00:00"
    }
  ]
}
```

Backed by the `UserHospital` junction table. Includes **all** hospital patients — those with a doctor bond and hospital-only patients (no doctor) alike. A user linked to multiple hospitals appears under each.

---

### GET `/hospitals/{hospital_id}/doctors`

Get all doctors affiliated with this hospital and their active patient counts.

Requires `Authorization: Bearer <token>`; path `hospital_id` must match JWT `sub`. Disabled hospitals return `403`.

Backed by the `DoctorHospital` junction table. A doctor affiliated with multiple hospitals appears under each.

**Response:**

```json
{
  "hospital_id": "HOSP_xxx",
  "doctors": [
    {
      "doctor": { "doctor_id": "dr_xxx", "doctorname": "Dr. Rao", "...": "..." },
      "patient_count": 12
    }
  ],
  "count": 1
}
```

---

### GET `/hospitals/{hospital_id}/relations`

Get all active doctor-patient assignments in a hospital.

Requires `Authorization: Bearer <token>`; path `hospital_id` must match JWT `sub`. Disabled hospitals return `403`.

**Response:**

```json
{
  "hospital_id": "HOSP_xxx",
  "relations": [
    {
      "user_id": "usr_xxx",
      "doctor_id": "dr_xxx",
      "hospital_id": "HOSP_xxx",
      "relation_type": "HOSPITAL_ASSIGNED",
      "status": "ACTIVE",
      "linked_by": "hospital_staff",
      "created_at": "2026-06-28T...",
      "updated_at": "2026-06-28T..."
    }
  ],
  "count": 1
}
```

Returns only bonds whose `hospital_id` attribute matches this hospital (i.e. assigned here). `relation_id` is no longer part of the response.

---

### GET `/hospitals/users/{user_id}/doctors`

Get all active doctors linked to a user. No auth required.

**Response:**

```json
{
  "user_id": "usr_xxx",
  "doctors": [
    {
      "doctor": { "doctor_id": "dr_xxx", "doctorname": "Dr. Rao", "...": "..." },
      "relation": { "user_id": "usr_xxx", "doctor_id": "dr_xxx", "status": "ACTIVE", "relation_type": "HOSPITAL_ASSIGNED", "...": "..." }
    }
  ],
  "count": 1
}
```

---

### GET `/hospitals/users/{user_id}/diagnosis-summary`

Fetch diagnosis fields stored on the `Users` row (populated by the Medilocker insurance autofill flow).

**Response:**

```json
{
  "user_id": "USR_123",
  "primary_diagnosis": "Dengue",
  "primary_icd_code": "A97",
  "additional_diagnosis": "Abdominal Pain, vomiting, Nausea, Headache, multiple Joint Pain",
  "additional_icd_code": "R10.85",
  "diagnosis_updated_at": "2026-05-13T11:00:00+00:00"
}
```

Fields not yet saved are returned as `null`. Returns `404` if the user does not exist.

---

### GET `/hospitals/users/{user_id}/doctors/{doctor_id}/relation`

Check whether a user is actively linked to a specific doctor.

**Response:**

```json
{
  "user_id": "usr_xxx",
  "doctor_id": "dr_xxx",
  "exists": true,
  "relation": { "user_id": "usr_xxx", "doctor_id": "dr_xxx", "status": "ACTIVE", "relation_type": "HOSPITAL_ASSIGNED", "...": "..." }
}
```

---

### DELETE `/hospitals/users/{user_id}/doctors/{doctor_id}`

Remove a doctor from a user by setting the active relation status to `INACTIVE`.

**Response:**

```json
{
  "user_id": "usr_xxx",
  "doctor_id": "dr_xxx",
  "relation": { "user_id": "usr_xxx", "doctor_id": "dr_xxx", "status": "INACTIVE", "...": "..." },
  "message": "Doctor removed from user"
}
```

---

### GET `/hospitals/doctors/{doctor_id}/patients`

Get all active patients assigned to a doctor. No auth required.

**Response:**

```json
{
  "doctor_id": "dr_xxx",
  "patients": [
    {
      "user": { "user_id": "usr_xxx", "name": "Rahul Sharma", "...": "..." },
      "relation": { "user_id": "usr_xxx", "doctor_id": "dr_xxx", "status": "ACTIVE", "relation_type": "HOSPITAL_ASSIGNED", "...": "..." }
    }
  ],
  "count": 1
}
```

---

### GET `/hospitals/doctors/{doctor_id}/patients/count`

Get the unique active patient count for a doctor. No auth required.

**Response:**

```json
{
  "doctor_id": "dr_xxx",
  "count": 1
}
```

---

## Staff Endpoints

All staff endpoints require `Authorization: Bearer <token>` from `POST /hospitals/login`. The `hospital_id` in every request must match the JWT `sub`. Disabled hospitals return `403`.

---

### POST `/hospitals/staff/add-patient`

Add a single patient with three mandatory documents. Returns `201`.

**Content-Type:** `multipart/form-data`

| Field              | Type    | Required | Description                                              |
| ------------------ | ------- | -------- | -------------------------------------------------------- |
| `hospital_id`      | string  | ✅        | Must match JWT `sub`                                     |
| `phone`            | string  | ✅        | Patient phone (E.164 or local digits)                    |
| `name`             | string  | ✅        | Patient display name                                     |
| `doctor_id`        | string  | —        | Attending doctor; must belong to this hospital           |
| `email`            | string  | —        | Patient email                                            |
| `age`              | integer | —        | Patient age (0–150)                                      |
| `gender`           | string  | —        | Patient gender                                           |
| `insurer`          | string  | —        | Insurance company / payer name; whitespace-only ignored  |
| `insurance_policy` | file    | ✅        | PDF or image                                             |
| `hospital_bill`    | file    | ✅        | PDF or image                                             |
| `prescription`     | file    | ✅        | PDF or image                                             |

Allowed file types: PDF, JPG, PNG, HEIC/HEIF, WebP; max ~10 MB per file. Files are stored under the patient's Medilocker S3 prefix and OCR is enqueued asynchronously.

**Behavior:**

- **With `doctor_id`:** doctor must be affiliated with this hospital (checked via `DoctorHospital` junction). Creates/links user by phone. A `UserDoctor` bond (`HOSPITAL_ASSIGNED`) and a `UserHospital` membership row are written — both additive, so existing affiliations with other hospitals are preserved.
- **Without `doctor_id`:** hospital-only patient — a `UserHospital` membership row is written; no `UserDoctor` bond is created.
- `insurer`, `age`, `gender` are written only when a **new** user row is created. Existing users (`status: "linked"`) are not updated for these fields — only their relation/hospital affiliation is ensured.

**Response:**

```json
{
  "status": "created",
  "user": {
    "user_id": "usr_...",
    "phoneNumber": "+919876543210",
    "name": "Rahul Sharma",
    "email": "rahul@example.com",
    "age": 42,
    "gender": "Male",
    "insurer": "Acme Health Insurance",
    "hospital_id": "HOSP_1A2B3C4D",
    "hospital_name": "City General Hospital",
    "source": "hospital_import",
    "createdAt": "2026-03-29T..."
  },
  "documents": [
    {
      "doc_type": "INSURANCE_POLICY",
      "document_category": "INSURANCE_POLICY",
      "file_id": "a1b2c3d4",
      "s3_original_key": "Medilocker/Users/usr_.../..."
    }
  ]
}
```

`status` is `"created"` for a new user or `"linked"` for an existing patient. `documents` always contains one entry per uploaded file.

---

### POST `/hospitals/staff/update_patient`

Partially update an existing hospital patient. **Note:** path uses underscore (`update_patient`), not a hyphen — there is no hyphenated alias.

**Content-Type:** `multipart/form-data`

| Field              | Type    | Required | Description                                                    |
| ------------------ | ------- | -------- | -------------------------------------------------------------- |
| `hospital_id`      | string  | ✅        | Must match JWT `sub`                                           |
| `user_id`          | string  | ✅        | Patient to update                                              |
| `doctor_id`        | string  | —        | Doctor to assign (see rules below)                             |
| `name`             | string  | —        |                                                                |
| `email`            | string  | —        |                                                                |
| `age`              | integer | —        | 0–150                                                          |
| `gender`           | string  | —        |                                                                |
| `insurer`          | string  | —        | Insurance company name                                         |
| `policy_number`    | string  | —        | Insurance policy number                                        |
| `insurance_policy` | file    | —        | New/updated insurance policy (PDF or image, ≤10 MB)            |
| `hospital_bill`    | file    | —        | New/updated hospital bill                                      |
| `prescription`     | file    | —        | New/updated prescription                                       |

Protected fields (`phoneNumber`, `hospital_id`, `hospital_name`, `createdAt`, `source`) are not writable through this endpoint.

**Doctor assignment rules:**

- `doctor_id` omitted → existing relation is untouched (`doctor_action: "none"`)
- `doctor_id` matches the current hospital-assigned doctor → no change (`doctor_action: "unchanged"`)
- `doctor_id` is a different doctor → old hospital-assigned relation deactivated, new one created (`doctor_action: "updated"`); new doctor must belong to the same hospital

Uploaded documents are added as new versions (no old record deleted). OCR is enqueued asynchronously.

**Response:**

```json
{
  "status": "updated",
  "updated_fields": ["age", "insurer", "name", "policy_number", "updatedAt"],
  "doctor_action": "updated",
  "user": {
    "user_id": "usr_...",
    "name": "Rahul Sharma",
    "age": 43,
    "insurer": "Acme Health Insurance",
    "policy_number": "POL-12345",
    "hospital_id": "HOSP_1A2B3C4D"
  },
  "documents": [
    {
      "doc_type": "PRESCRIPTION",
      "document_category": "PRESCRIPTION",
      "file_id": "a1b2c3d4",
      "s3_original_key": "Medilocker/Users/usr_.../a1b2c3d4/original.pdf"
    }
  ]
}
```

`doctor_action` is one of `"none"`, `"unchanged"`, or `"updated"`. `documents` is only present when at least one file was uploaded.

---

### POST `/hospitals/staff/add-doctor`

Add a single doctor. Returns `201`. Creates doctor profile + auth + default availability slots, or links an existing doctor to the hospital.

**Content-Type:** `application/json`

**Body:**

```json
{
  "hospital_id": "HOSP_1A2B3C4D",
  "phone": "+919876543210",
  "name": "Dr. Priya Patel",
  "specialization": "Cardiology",
  "experience": "10 years",
  "email": "priya@hospital.com"
}
```

Required: `hospital_id`, `phone`, `name`, `specialization`, `experience` (free-text string). Optional: `email`.

**Response (new doctor):**

```json
{
  "status": "created",
  "doctor": {
    "doctor_id": "dr_...",
    "phoneNumber": "+919876543210",
    "doctorname": "Dr. Priya Patel",
    "specialization": "Cardiology",
    "experience": "10 years",
    "hospital_id": "HOSP_1A2B3C4D",
    "hospital_name": "City General Hospital",
    "source": "hospital_import",
    "createdAt": "2026-03-29T..."
  }
}
```

**Response (existing doctor linked):**

```json
{
  "status": "linked",
  "doctor": { "doctor_id": "dr_...", "...": "..." }
}
```

---

### POST `/hospitals/staff/import-patients`

Stage a patient Excel file (.xlsx) for asynchronous processing (typically within 24 hours). Returns `202 Accepted`. The file is not processed inline — no row-level summaries are returned.

**Content-Type:** `multipart/form-data`

| Field         | Required | Description                                                                           |
| ------------- | -------- | ------------------------------------------------------------------------------------- |
| `hospital_id` | ✅        | Must match JWT `sub`; hospital must be active                                         |
| `doctor_id`   | ✅        | Attending doctor; must belong to this hospital                                        |
| `file`        | ✅        | `.xlsx` file; columns expected by processor: `phone` (required), `name` (required), `email` (optional) |

**Response `202`:**

```json
{
  "status": "accepted",
  "import_kind": "patient",
  "message": "Your file was received. Data will typically be live within 24 hours.",
  "s3_key": "hospital_staff/patient_imports/HOSP_.../dr_.../20260331T120000Z_abc123def456.xlsx",
  "manifest_key": "hospital_staff/patient_imports/HOSP_.../dr_.../20260331T120000Z_abc123def456.manifest.json",
  "staging_id": "20260331T120000Z_abc123def456",
  "eta_hours": 24
}
```

API Gateway / Lambda payload limit (~6 MB) applies.

---

### POST `/hospitals/staff/import-doctors`

Stage a doctor Excel file (.xlsx) for asynchronous processing (typically within 24 hours). Returns `202 Accepted`. No inline row summaries.

**Content-Type:** `multipart/form-data`

| Field         | Required | Description                                                                                                                    |
| ------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `hospital_id` | ✅        | Must match JWT `sub`; hospital must be active                                                                                  |
| `file`        | ✅        | `.xlsx` file; columns expected by processor: `phone` (required), `name` (required), `specialization` (required), `experience` (required), `email` (optional) |

**Response `202`:**

```json
{
  "status": "accepted",
  "import_kind": "doctor",
  "message": "Your file was received. Data will typically be live within 24 hours.",
  "s3_key": "hospital_staff/doctor_imports/HOSP_.../20260331T120000Z_abc123def456.xlsx",
  "manifest_key": "hospital_staff/doctor_imports/HOSP_.../20260331T120000Z_abc123def456.manifest.json",
  "staging_id": "20260331T120000Z_abc123def456",
  "eta_hours": 24
}
```

API Gateway / Lambda payload limit (~6 MB) applies.
