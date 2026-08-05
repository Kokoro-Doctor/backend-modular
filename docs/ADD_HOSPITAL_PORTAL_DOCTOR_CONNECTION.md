# Adding a New Hospital Portal with Doctor–Hospital Connection

This document outlines what you need to add to integrate a new hospital portal into the existing Kokoro flow, with proper **doctor–hospital connection** support.

---

## Current State Summary

| Component | Status |
|-----------|--------|
| **Hospital file upload** | Exists via `/hospital/*` (API key auth, presigned URLs) |
| **Hospital entity** | No Hospitals table; `hospital_id` is client-provided string |
| **Doctor–hospital relationship** | Only `affiliation` free-text on Doctors table; no join table |
| **Doctor–user connection** | Documented in [DR_USER_CONNECTION_FLOW.md](./DR_USER_CONNECTION_FLOW.md) |
| **Patient hospital booking** | AllHospitals uses hardcoded list; separate from doctor flow |

---

## 1. Database Changes

### 1.1 Hospitals Table (new)

Create a Hospitals table for registered hospital entities.

| Attribute | Type | Key | Description |
|-----------|------|-----|-------------|
| `hospital_id` | string | PK | Partition key (e.g. `HOSP_001`) |
| `name` | string | | Hospital name |
| `address` | string | | Address |
| `phone` | string | | Contact phone |
| `email` | string | | Contact email |
| `api_key_hash` | string | | Hashed API key (for per-hospital auth) |
| `status` | string | | `ACTIVE` \| `PENDING` \| `SUSPENDED` |
| `created_at` | string | | ISO 8601 timestamp |
| `updated_at` | string | | ISO 8601 timestamp |

**Optional GSI:** `email-index` for lookup by email.

### 1.2 DoctorHospital Table (new)

Junction table for doctor–hospital relationship.

| Attribute | Type | Key | Description |
|-----------|------|-----|-------------|
| `doctor_id` | string | PK | Partition key |
| `hospital_id` | string | SK | Sort key (e.g. `HOSP_001`) |
| `status` | string | | `PENDING` \| `APPROVED` \| `REJECTED` |
| `requested_by` | string | | `doctor` \| `hospital` |
| `requested_at` | string | | ISO 8601 timestamp |
| `approved_at` | string | | ISO 8601 timestamp (optional) |
| `role` | string | | e.g. `ATTENDING` \| `CONSULTANT` (optional) |

**GSI:** `hospital_id-index` (PK: `hospital_id`, SK: `doctor_id`) for querying doctors by hospital.

### 1.3 HospitalFiles Table (existing)

Update schema to optionally store `doctor_id` for files uploaded by hospital on behalf of a doctor:

| Attribute | Type | Key | Description |
|-----------|------|-----|-------------|
| `hospital_id` | string | PK | (existing) |
| `file_id` | string | SK | (existing) |
| `patient_id` | string | | (existing) |
| `doctor_id` | string | | **New** – optional, links file to a connected doctor |
| ... | | | (rest unchanged) |

---

## 2. Backend (Lambda) Changes

### 2.1 New: HospitalAuthLambda or extend AuthLambda

- **Hospital registration** – create hospital entity, generate API key
- **Hospital login** – validate API key, return session/token
- **Endpoints:**
  - `POST /auth/hospital/register` – Register new hospital (admin or self-service)
  - `POST /auth/hospital/login` – Login with API key (or email/password if you add that)

### 2.2 New: DoctorHospitalServiceLambda (or add to DoctorsServiceLambda)

- **Doctor–hospital connection flow**
- **Endpoints:**
  - `POST /doctorsService/doctor/{doctor_id}/hospital/request` – Doctor requests connection to hospital
    - Body: `{ "hospital_id": "string" }`
  - `POST /hospital/{hospital_id}/doctor/approve` – Hospital approves doctor connection
    - Body: `{ "doctor_id": "string" }`
  - `GET /doctorsService/doctor/{doctor_id}/hospitals` – List hospitals connected to doctor
  - `GET /hospital/{hospital_id}/doctors` – List doctors connected to hospital (requires hospital auth)
  - `DELETE /doctorsService/doctor/{doctor_id}/hospital/{hospital_id}` – Remove connection

### 2.3 Update: Hospital Router (MediLockerLambda)

- **Per-hospital API keys** – Support multiple hospitals with different keys (store in Hospitals table, validate against `api_key_hash`)
- **Optional `doctor_id`** in upload – When hospital uploads on behalf of a connected doctor:
  - `POST /hospital/upload` – Add optional form field `doctor_id`
  - `POST /hospital/presign-upload` – Add optional `doctor_id` in body
  - Validate that `doctor_id` is connected to `hospital_id` before saving

### 2.4 New: Hospital Portal API (optional)

If the hospital portal needs more than file upload:

- `GET /hospital/{hospital_id}/patients` – List patients (from HospitalFiles)
- `GET /hospital/{hospital_id}/files` – List files with optional filters
- `GET /hospital/{hospital_id}/doctors` – List connected doctors

---

## 3. Frontend Changes

### 3.1 Hospital Portal (new)

Create a new portal flow similar to Doctor Portal:

| Screen | Purpose |
|--------|---------|
| `HospitalLoginPage` | Login with API key or email/password |
| `HospitalDashboard` | Overview: connected doctors, recent uploads |
| `HospitalDoctorsList` | List connected doctors, approve/reject requests |
| `HospitalUploadPage` (existing) | Reuse; add `doctor_id` selector when doctor is connected |
| `HospitalPatientFiles` | View files by patient (optional) |

### 3.2 Doctor Portal (update)

| Screen | Purpose |
|--------|---------|
| `DoctorHospitalConnection` (new) | Request connection to hospital, view status |
| `DoctorSettings` | Add "Connected Hospitals" section |
| `DoctorPatientLandingPage` | Optionally show patients from hospital uploads |

### 3.3 Navigation

- **RootNavigator** – Add `HospitalAppNavigation` (similar to `DoctorAppNavigation`)
- **Linking** – Add `HospitalAppNavigation: { path: "hospital", screens: {...} }`
- **Auth flow** – After login, route by role: `user` → Patient, `doctor` → Doctor, `hospital` → Hospital

### 3.4 Auth Context

- Extend `useAuth` to support `role: "hospital"`
- Store `hospital_id` in session/context when hospital logs in

---

## 4. Auth & Roles

### 4.1 Role Types

| Role | Entity | Login method |
|------|--------|--------------|
| `user` | Patient | Phone OTP, Google OAuth |
| `doctor` | Doctor | Phone OTP, Google OAuth |
| `hospital` | Hospital | API key or email/password |

### 4.2 AuthTable / SessionsTable

- Add `hospital_id` as identity for hospital sessions
- Ensure `role` is set to `hospital` for hospital logins

---

## 5. Environment & Config

| Variable | Description |
|----------|-------------|
| `HOSPITALS_TABLE` | DynamoDB table for Hospitals |
| `DOCTOR_HOSPITAL_TABLE` | DynamoDB table for DoctorHospital |
| `HOSPITAL_API_KEYS` | If using single key: existing `HOSPITAL_API_KEY`; if multi-tenant: validate from Hospitals table |

---

## 6. SAM / Infrastructure (template.yaml)

- Add `HospitalsTable` resource
- Add `DoctorHospitalTable` resource (with GSI on `hospital_id`)
- Add `HospitalAuthLambda` (or extend AuthLambda)
- Add `DoctorHospitalServiceLambda` (or extend DoctorsServiceLambda)
- Add API Gateway routes for new endpoints
- Update MediLockerLambda env vars if using per-hospital keys

---

## 7. Implementation Checklist

### Phase 1: Database & Core API

- [ ] Create `HospitalsTable` in `template.yaml`
- [ ] Create `DoctorHospitalTable` in `template.yaml` (with GSI)
- [ ] Add hospital registration + login logic (AuthLambda or new)
- [ ] Add doctor–hospital request/approve/list endpoints

### Phase 2: Hospital Upload Integration

- [ ] Add optional `doctor_id` to hospital upload endpoints
- [ ] Validate doctor–hospital connection before saving `doctor_id`
- [ ] Update `HospitalFiles` schema to include `doctor_id`

### Phase 3: Frontend – Doctor Portal

- [ ] Add "Request Hospital Connection" screen
- [ ] Add "Connected Hospitals" in DoctorSettings
- [ ] Show hospital-linked patients/files in doctor dashboard (optional)

### Phase 4: Frontend – Hospital Portal

- [ ] Add HospitalLoginPage
- [ ] Add HospitalDashboard
- [ ] Add HospitalDoctorsList (approve/reject requests)
- [ ] Update HospitalUploadPage with doctor selector
- [ ] Add HospitalAppNavigation and routing

### Phase 5: Auth & Multi-Role

- [ ] Extend auth to support `hospital` role
- [ ] Update RootNavigator to route by role
- [ ] Add hospital session handling in AuthContext

---

## 8. Sequence Diagram: Doctor–Hospital Connection

```
Doctor                    System                     Hospital
  |                         |                            |
  |-- Request connection -->|                            |
  |   (hospital_id)         |                            |
  |                         |-- Create DoctorHospital --->|
  |                         |   status=PENDING           |
  |<-- "Request sent" ------|                            |
  |                         |                            |
  |                         |<-- Login (API key) --------|
  |                         |<-- View pending doctors ---|
  |                         |-- List PENDING ---------->|
  |                         |                            |
  |                         |<-- Approve doctor_id -----|
  |                         |-- Update status=APPROVED ->|
  |                         |                            |
  |-- View "Connected" ----->|                            |
  |<-- List hospitals ------|                            |
```

---

## 9. Related Docs

- [DR_USER_CONNECTION_FLOW.md](./DR_USER_CONNECTION_FLOW.md) – Doctor–user subscription flow
- [HOSPITAL_RAW_INGESTION.md](../backend/medilocker_lambda/docs/HOSPITAL_RAW_INGESTION.md) – Current hospital upload API
- [LAMBDA_FUNCTIONS.md](./LAMBDA_FUNCTIONS.md) – API endpoints overview
- [DATABASE_TABLES.md](./DATABASE_TABLES.md) – Database schema overview
