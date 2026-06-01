# Hospitals Lambda

Public hospital CRUD and auth paths. Several routes require a JSON **body** even for `GET`/`PUT`; `hospital_id` in the body must match the path where both are present.

---

### POST `/hospitals/signup`

Register a new hospital (returns `201`). At least one of `email` or `contact_number` is required. If both are provided, `email` becomes the primary login identifier.

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

`hospital_id` is auto-generated as `HOSP_<uuid>`. Password is stored as a bcrypt hash; the plain-text value is never persisted.

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

A JWT is issued immediately on signup so the hospital is authenticated without a separate login call. `password_hash` is never returned in any response.

---

### POST `/hospitals/login`

Authenticate using email or mobile number and password. Returns hospital record plus a **JWT** for staff routes.

**Body:**

```json
{
  "identifier": "contact@hospital.com",
  "password": "SecurePassword123"
}
```

`identifier` can be either an email address or a mobile number. The server detects which by checking for `@`.

**Response:**

```json
{
  "hospital": { "hospital_id": "HOSP_xxx", "name": "...", ... },
  "token": "eyJ...",
  "expires_in": 28800,
  "message": "Login successful"
}
```

(`expires_in` is seconds; default expiry is 8 hours.)

---

### GET `/hospitals/list`

List all **active** hospitals. No request body.

**Response:**

```json
{
  "hospitals": [{ "hospital_id": "HOSP_...", "name": "...", "...": "..." }]
}
```

---

### GET `/hospitals/{hospital_id}/patients`

List unique users assigned through this hospital's **active** doctor-patient relations. Requires `**Authorization: Bearer <token>`** from `POST /hospitals/login`; path `hospital_id` must match JWT `sub`. Disabled hospitals receive `403`.

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

**Note:** Backed by `UserDoctorRelations`. Hospital-only patients with no doctor relation are not included in this relation view.

---

### GET `/hospitals/users/{user_id}/doctors`

Get all active doctors linked to a user.

**Response:**

```json
{
  "user_id": "usr_xxx",
  "doctors": [
    {
      "doctor": { "doctor_id": "dr_xxx", "doctorname": "Dr. Rao", "...": "..." },
      "relation": { "relation_id": "uuid", "status": "ACTIVE", "...": "..." }
    }
  ],
  "count": 1
}
```

---

### GET `/hospitals/users/{user_id}/diagnosis-summary`

Fetch diagnosis fields stored on the `Users` row by Medilocker insurance stored autofill.

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

If diagnosis fields have not been saved yet, the fields are returned as `null`. If the user does not exist, the endpoint returns `404`.

---

### GET `/hospitals/users/{user_id}/doctors/{doctor_id}/relation`

Check whether a user is actively linked to a specific doctor. Returns the active relation if one exists.

**Response:**

```json
{
  "user_id": "usr_xxx",
  "doctor_id": "dr_xxx",
  "exists": true,
  "relation": { "relation_id": "uuid", "status": "ACTIVE", "...": "..." }
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
  "relation": { "relation_id": "uuid", "status": "INACTIVE", "...": "..." },
  "message": "Doctor removed from user"
}
```

---

### GET `/hospitals/doctors/{doctor_id}/patients`

Get all active patients assigned to a doctor.

**Response:**

```json
{
  "doctor_id": "dr_xxx",
  "patients": [
    {
      "user": { "user_id": "usr_xxx", "name": "Rahul Sharma", "...": "..." },
      "relation": { "relation_id": "uuid", "status": "ACTIVE", "...": "..." }
    }
  ],
  "count": 1
}
```

---

### GET `/hospitals/doctors/{doctor_id}/patients/count`

Get the unique active patient count for a doctor.

**Response:**

```json
{
  "doctor_id": "dr_xxx",
  "count": 1
}
```

---

### GET `/hospitals/{hospital_id}/doctors`

Get all doctors in a hospital with active assigned patient counts. Requires `**Authorization: Bearer <token>**` from `POST /hospitals/login`; path `hospital_id` must match JWT `sub`.

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

Get all active doctor-patient assignments in a hospital. Requires `**Authorization: Bearer <token>**` from `POST /hospitals/login`; path `hospital_id` must match JWT `sub`.

**Response:**

```json
{
  "hospital_id": "HOSP_xxx",
  "relations": [
    {
      "relation_id": "uuid",
      "user_id": "usr_xxx",
      "doctor_id": "dr_xxx",
      "hospital_id": "HOSP_xxx",
      "relation_type": "HOSPITAL_ASSIGNED",
      "status": "ACTIVE",
      "linked_by": "hospital_staff"
    }
  ],
  "count": 1
}
```

---

### POST `/hospitals/relations`

Create or reactivate a user-doctor relation for a hospital. Requires `**Authorization: Bearer <token>**` from `POST /hospitals/login`; body `hospital_id` must match JWT `sub`. Doctor must belong to the hospital.

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

**Response:**

```json
{
  "relation": {
    "relation_id": "uuid",
    "user_id": "usr_xxx",
    "doctor_id": "dr_xxx",
    "hospital_id": "HOSP_xxx",
    "relation_type": "HOSPITAL_ASSIGNED",
    "status": "ACTIVE",
    "linked_by": "hospital_staff"
  }
}
```

---

### GET `/hospitals/get/{hospital_id}`

Get one hospital. Path `hospital_id` and body `hospital_id` must match.

**Body:**

```json
{
  "hospital_id": "HOSP_12345678-1234-1234-1234-123456789012"
}
```

**Response:**

```json
{
  "hospital": { "hospital_id": "HOSP_xxx", "name": "...", ... }
}
```

---

### PUT `/hospitals/update/{hospital_id}`

Update hospital metadata. Path and body `hospital_id` must match.

**Body (only `hospital_id` required; other fields optional):**

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

Soft-delete (deactivate) a hospital. Path and body `hospital_id` must match.

**Body:**

```json
{
  "hospital_id": "HOSP_12345678-1234-1234-1234-123456789012"
}
```

**Response:**

```json
{
  "hospital": { "hospital_id": "HOSP_xxx", "is_active": false, "...": "..." }
}
```

---

### POST `/hospitals/staff/add-patient`

Add a single patient. Requires `Authorization: Bearer <token>` from `POST /hospitals/login`. Form field `hospital_id` must match the JWT `sub`. Field names and validation match the server's `AddPatientForm` (multipart) / `AddPatientRequest` (reference model for the same scalar fields).

**Content-Type:** `multipart/form-data`

**Form fields:**

- `hospital_id` (required) — must match token
- `phone`, `name` (required)
- `doctor_id` (optional) — if set, doctor must belong to this hospital
- `email`, `age` (0–150), `gender`, `**insurer`** (optional) — free-text insurer or payer name; whitespace-only values are ignored
- `insurance_policy` (file, required) — PDF or image
- `hospital_bill` (file, required) — PDF or image
- `prescription` (file, required) — PDF or image

Allowed types per file are enforced server-side (e.g. PDF, JPG, PNG, HEIC/HEIF, WebP; max size typically 10 MB). Files are stored under the patient’s Medilocker prefix and OCR is queued asynchronously.

**Behavior:**

- **With `doctor_id`:** Doctor must exist and have this `hospital_id`. Creates/links user by phone; `UserDoctorRelations` (`HOSPITAL_ASSIGNED`) uses the session hospital; `**Users.hospital_id` and `Users.hospital_name` are set from the session hospital** (new rows include them on create; existing users get them via update).
- **Without `doctor_id`:** Patient under hospital only: sets `Users.hospital_id` and `hospital_name` from the session hospital; no `UserDoctorRelations` row.
- `**insurer` (and `age` / `gender`):** Written to `Users` only when a **new** user row is created for this phone. If the phone already matches an existing user (`status: "linked"`), the existing DynamoDB item is **not** updated from this request — relation/hospital affiliation is ensured only.

**Response:** Same `status` / `user` shape as before, plus a `documents` array (one entry per uploaded doc: e.g. `doc_type`, `document_category`, `file_id`, `s3_original_key`).

Example (abbreviated):

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

`UserDoctorRelations` stores `hospital_id` from the staff session when a doctor is specified. `**Users` also stores `hospital_id` / `hospital_name**` for that path (aligned with hospital-only adds).

**Response (existing patient linked):** Same structure with `"status": "linked"` and `documents` for the three files.

---

### POST `/hospitals/staff/update_patient`

Partially update an existing hospital patient. Requires `Authorization: Bearer <token>` from `POST /hospitals/login`. `hospital_id` must match the JWT `sub`.

**Path:** underscore only (`update_patient`). There is no hyphenated alias.

**Content-Type:** `multipart/form-data`

`user_id` and `hospital_id` are required. All other fields are optional — only fields present in the request are written to the `Users` row. Field names match the server's `UpdatePatientForm` / `UpdatePatientRequest` (excluding file parts, which are multipart-only). Protected ownership/identity fields (`phoneNumber`, `hospital_id`, `hospital_name`, `createdAt`, `source`) are not writable through this endpoint.

**Form fields:**


| Field              | Type    | Required | Description                                                  |
| ------------------ | ------- | -------- | ------------------------------------------------------------ |
| `hospital_id`      | string  | ✅        | Must match JWT `sub`                                         |
| `user_id`          | string  | ✅        | Patient to update                                            |
| `doctor_id`        | string  | —        | Doctor to assign (see rules below)                           |
| `name`             | string  | —        | Patient display name                                         |
| `email`            | string  | —        | Patient email                                                |
| `age`              | integer | —        | Patient age (0–150)                                          |
| `gender`           | string  | —        | Patient gender                                               |
| `insurer`          | string  | —        | Insurance company name                                       |
| `policy_number`    | string  | —        | Insurance policy number                                      |
| `insurance_policy` | file    | —        | New/updated insurance policy document (PDF or image, ≤10 MB) |
| `hospital_bill`    | file    | —        | New/updated hospital bill                                    |
| `prescription`     | file    | —        | New/updated prescription                                     |


**Doctor assignment rules:**

- `doctor_id` not provided → existing doctor link is untouched.
- `doctor_id` matches the current hospital-assigned doctor → no change (`doctor_action: "unchanged"`).
- `doctor_id` is a different doctor → old hospital-assigned relation is deactivated, new relation is created (`doctor_action: "updated"`). The new doctor must belong to the same hospital.

Documents, when provided, are uploaded to S3 and OCR is enqueued asynchronously (same pipeline as `/add-patient`). Multiple new versions accumulate — no old record is deleted.

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

`doctor_action` is one of `"none"` (no doctor_id given), `"unchanged"` (same doctor), or `"updated"` (doctor was replaced). `documents` key is only present when at least one file was uploaded.

---

### POST `/hospitals/staff/add-doctor`

Add a single doctor. Requires `Authorization: Bearer <token>`. JSON body `hospital_id` must match the JWT `sub`. Creates doctor profile + auth + default slots, or links an existing doctor to the hospital.

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

Accepts a patient Excel file (.xlsx) under an **attending doctor** and **stages** it in S3 for asynchronous processing (typically within 24 hours). Multipart form data. Requires `Authorization: Bearer <token>` from `POST /hospitals/login`. Form `hospital_id` must match the JWT. The doctor must belong to that hospital and the hospital must be active. The workbook is **not** processed inline; row-level summaries are no longer returned (breaking change for clients that expected `total_rows` / `created` / etc.).

**Form fields:**

- `hospital_id`: `HOSP_...` (must match token)
- `doctor_id`: `dr_xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`
- `file`: Excel file (.xlsx), non-empty; with columns expected by the downstream processor: `phone` (required), `name` (required), `email` (optional)

**Response:** `202 Accepted`

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

**Note:** API Gateway / Lambda request payload limits (~6 MB) apply to uploads.

---

### POST `/hospitals/staff/import-doctors`

Accepts a doctor Excel file (.xlsx) and **stages** it in S3 for asynchronous processing (typically within 24 hours). Multipart form data. Requires `Authorization: Bearer <token>`; form `hospital_id` must match the JWT. The hospital must be active. Inline row summaries are no longer returned (breaking change).

**Form fields:**

- `hospital_id`: `HOSP_1A2B3C4D` (must match token)
- `file`: Excel file (.xlsx), non-empty; with columns expected by the downstream processor: `phone` (required), `name` (required), `specialization` (required), `experience` (required), `email` (optional)

**Response:** `202 Accepted`

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

**Note:** API Gateway / Lambda request payload limits (~6 MB) apply to uploads.

---

