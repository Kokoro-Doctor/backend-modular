# Database Tables Overview

This document describes all DynamoDB tables used by the Kokoro platform, including key schema, attributes, and usage.

---

## Authentication & Users

### Users

Patient profiles, login data, subscription references.

| Attribute | Type | Key | Required | Description |
|-----------|------|-----|----------|-------------|
| user_id | S | PK | Yes | Partition key |
| phoneNumber | S | GSI | Yes | Phone (phone-index) |
| email | S | GSI | No | Email (email-index) |
| name | S | — | No | Display name |
| username | S | — | No | Alias (Google auth) |
| picture | S | — | No | Profile photo URL (Google auth) |
| createdAt | S | — | Yes | ISO 8601 timestamp |
| source | S | — | No | e.g. `doctor_import` |
| hospital_id | S | GSI | No | Hospital affiliation for hospital-created/linked patients |
| hospital_name | S | — | No | Denormalized hospital name |
| age | N | — | No | Patient age |
| gender | S | — | No | Patient gender |
| insurer | S | — | No | Insurance company name |
| policy_number | S | — | No | Insurance policy number |
| primary_diagnosis | S | — | No | Latest diagnosis from insurance stored autofill |
| primary_icd_code | S | — | No | ICD code for latest primary diagnosis |
| additional_diagnosis | S | — | No | Latest additional diagnosis from insurance stored autofill |
| additional_icd_code | S | — | No | ICD code for latest additional diagnosis |
| diagnosis_updated_at | S | — | No | UTC ISO 8601 timestamp for diagnosis fields |
| updatedAt | S | — | No | ISO 8601 timestamp |

**GSI:** `email-index` (PK: email), `phone-index` (PK: phoneNumber), `hospital_id-index` (PK: hospital_id)

---

### Doctors

Doctor profiles, onboarding status, credentials, affiliation.

| Attribute | Type | Key | Required | Description |
|-----------|------|-----|----------|-------------|
| doctor_id | S | PK | Yes | Partition key |
| phoneNumber | S | GSI | Yes | Phone |
| email | S | GSI | No | Email |
| doctorname | S | — | No | Display name (also `name` in responses) |
| specialization | S | — | No | Medical specialty |
| experience | N | — | No | Years of experience |
| description | S | — | No | Bio |
| fees | N | — | No | Consultation fee |
| timings | S | — | No | Availability hours |
| licenseNumber | S | — | No | Medical license |
| registrationId | S | — | No | Registration ID |
| affiliation | S | — | No | Hospital/clinic (free-text) |
| hospital_id | S | — | No | Linked hospital ID (HOSP_xxx) |
| hospital_name | S | — | No | Denormalized hospital name |
| degreeCertificate | S | — | No | S3 URL |
| govtIdProof | S | — | No | S3 URL |
| profilePhoto | S | — | No | S3 URL |
| onboarded | B | — | No | True if profile completed |
| createdAt | S | — | Yes | ISO 8601 timestamp |

**GSI:** `email-index` (PK: email), `phone-index` (PK: phoneNumber)

---

### Hospitals

Admin-managed hospitals. Doctors link via `hospital_id`.

| Attribute | Type | Key | Required | Description |
|-----------|------|-----|----------|-------------|
| hospital_id | S | PK | Yes | Partition key (HOSP_&lt;8_char_uuid&gt;) |
| name | S | — | Yes | Hospital name |
| api_key | S | — | Yes | Hospital login credential (private; stripped from public responses) |
| address | S | — | No | Address |
| city | S | — | No | City |
| state | S | — | No | State |
| contact_number | S | — | No | Contact number |
| email | S | — | No | Email |
| created_at | S | — | Yes | ISO 8601 timestamp |
| is_active | B | — | Yes | True if active |

---

### AuthTable

Phone-first identity store. Links phone to user or doctor.

| Attribute | Type | Key | Required | Description |
|-----------|------|-----|----------|-------------|
| phoneNumber | S | PK | Yes | Partition key |
| email | S | GSI | No | Email (email-index) |
| role | S | — | No | `user` \| `doctor` |
| user_id | S | — | No | Linked user ID |
| doctor_id | S | — | No | Linked doctor ID |
| is_verified | B | — | No | Overall verification |
| phone_verified | B | — | No | Phone verified |
| email_verified | B | — | No | Email verified |
| created_at | S | — | No | ISO 8601 |
| updated_at | S | — | No | ISO 8601 |
| last_login | S | — | No | ISO 8601 |
| last_verified_at | S | — | No | ISO 8601 |

**GSI:** `email-index` (PK: email)

---

### AuthTokensTable

OTP and password reset tokens. TTL enabled.

| Attribute | Type | Key | Required | Description |
|-----------|------|-----|----------|-------------|
| token_id | S | PK | Yes | Partition key |
| purpose | S | SK | Yes | Sort key (e.g. `login_otp`, `signup_otp`, `rate_limit#mobile_otp`) |
| token | S | — | Yes | OTP code |
| phoneNumber | S | GSI | No | Phone (phone-index) |
| email | S | GSI | No | Email (email-index) |
| role | S | — | No | `user` \| `doctor` |
| ttl | N | — | Yes | Unix timestamp (TTL) |
| createdAt | S | — | Yes | ISO 8601 |

**GSI:** `email-index` (PK: email, SK: purpose), `phone-index` (PK: phoneNumber, SK: purpose), **TTL:** ttl

---

### SessionsTable

Anonymous session identifiers for unauthenticated users. 7-day TTL.

| Attribute | Type | Key | Required | Description |
|-----------|------|-----|----------|-------------|
| session_id | S | PK | Yes | Partition key |
| created_at | S | — | No | ISO 8601 |
| ttl | N | — | Yes | Unix timestamp (TTL) |

**TTL:** ttl

---

## Appointments & Availability

### DoctorAvailabilityTable

Doctor time slots by date. PK = doctor_id, SK = date#slot_time.

| Attribute | Type | Key | Required | Description |
|-----------|------|-----|----------|-------------|
| PK | S | PK | Yes | doctor_id |
| SK | S | SK | Yes | `{date}#{slot_time}` e.g. `2025-03-19#10:00` |
| available | B | — | Yes | True if slot is free |
| user_id | S | — | No | Set when booked |
| booking_id | S | — | No | Set when booked |
| meet_link | S | — | No | Jitsi link when booked |
| created_at | S | — | No | ISO 8601 |
| expiry_timestamp | N | — | No | TTL for expiry |

**TTL:** expiry_timestamp

---

### AppointmentsTable

Patient bookings/appointments. PK = doctor_id, SK = date#start_time.

| Attribute | Type | Key | Required | Description |
|-----------|------|-----|----------|-------------|
| PK | S | PK | Yes | doctor_id |
| SK | S | SK | Yes | `{date}#{start_time}` e.g. `2025-03-19#10:00` |
| doctor_id | S | — | Yes | Doctor ID |
| date | S | — | Yes | YYYY-MM-DD |
| start_time | S | — | Yes | HH:MM |
| user_id | S | — | Yes | Patient ID |
| booking_id | S | — | Yes | UUID |
| meet_link | S | — | No | Jitsi link |
| subscription_id | S | — | No | If used |
| created_at | S | — | No | ISO 8601 |

**GSI:** `GSI_UserBookings` (PK: user_id, SK: SK), `GSI_BookingId` (PK: booking_id)

---

## Subscriptions & Payments

### SubscriptionPlans

Subscription plan definitions. Immutable once created.

| Attribute | Type | Key | Required | Description |
|-----------|------|-----|----------|-------------|
| plan_id | S | PK | Yes | Partition key (e.g. `PLAN_999_30D_ALL`) |
| doctor_id | S | — | Yes | Doctor ID or `ALL` |
| price | N | — | Yes | Price in INR |
| appointments_allowed | N | — | Yes | Appointments per plan |
| validity_days | N | — | Yes | Plan duration in days |
| valid_from | S | — | No | ISO date |
| valid_to | S | — | No | ISO date |
| is_active | B | — | Yes | Whether plan is active |
| created_at | S | — | No | ISO 8601 |

---

### UserDoctorSubscriptions

Active user subscriptions to doctors.

| Attribute | Type | Key | Required | Description |
|-----------|------|-----|----------|-------------|
| subscription_id | S | PK | Yes | Partition key (UUID) |
| user_id | S | GSI | Yes | Patient ID |
| doctor_id | S | GSI | Yes | Doctor ID |
| plan_id | S | — | Yes | Plan reference |
| plan_price | N | — | Yes | Snapshot at creation |
| appointments_total | N | — | Yes | Snapshot at creation |
| appointments_used | N | — | Yes | Count used |
| status | S | — | Yes | `ACTIVE` \| `CANCELLED` \| etc. |
| start_date | S | — | Yes | ISO 8601 |
| end_date | S | — | Yes | ISO 8601 |
| payment_id | S | GSI | Yes | Razorpay payment ID |
| created_at | S | — | No | ISO 8601 |

**GSI:** `GSI_UserSubscriptions` (PK: user_id), `GSI_DoctorSubscribers` (PK: doctor_id), `GSI_PaymentSubscription` (PK: payment_id)

---

### UserDoctor

Persistent patient-doctor connectivity graph. This is not an entitlement table; subscriptions still gate billing, booking limits, and plan validity.

| Attribute | Type | Key | Required | Description |
|-----------|------|-----|----------|-------------|
| user_id | S | PK | Yes | Patient ID |
| doctor_id | S | SK / GSI | Yes | Doctor ID |
| relation_type | S | — | Yes | `USER_SUBSCRIPTION` \| `HOSPITAL_ASSIGNED` \| `MANUAL` |
| status | S | — | Yes | `ACTIVE` \| `INACTIVE` |
| linked_by | S | — | Yes | `system` \| `hospital_staff` \| `doctor` |
| hospital_id | S | — | No | Set for hospital-assigned relations |
| subscription_id | S | — | No | Subscription that created/refreshed the relation |
| created_at | S | — | Yes | ISO 8601 |
| updated_at | S | — | Yes | ISO 8601 |

**GSI:** `GSI_DoctorUsers` (PK: doctor_id, SK: user_id)

One row is kept per `(user_id, doctor_id)` pair. Re-posting the same pair updates the existing row and sets it ACTIVE.

---

### PaymentsTable

Razorpay payment records and invoices.

| Attribute | Type | Key | Required | Description |
|-----------|------|-----|----------|-------------|
| payment_id | S | PK | Yes | Partition key (Razorpay ID) |
| order_id | S | — | No | Razorpay order ID |
| amount | N | — | Yes | Amount paid |
| currency | S | — | Yes | e.g. `INR` |
| status | S | — | Yes | e.g. `captured` |
| timestamp | S | — | No | ISO 8601 |
| invoice_url | S | — | No | Invoice URL |
| user_id | S | — | No | Patient ID |
| doctor_id | S | — | No | Doctor ID |
| plan_id | S | — | No | Plan ID |
| admin_email_sent | B | — | No | Admin notification flag |

---

## Doctor Earnings & Payouts

### DoctorEarningsLedger

Immutable financial ledger for doctor earnings. PK = doctor_id, SK = YYYY-MM#payment_id.

| Attribute | Type | Key | Required | Description |
|-----------|------|-----|----------|-------------|
| PK | S | PK | Yes | doctor_id |
| SK | S | SK | Yes | `{YYYY-MM}#{payment_id}` |
| user_id | S | — | Yes | Patient ID |
| subscription_id | S | — | Yes | Subscription ID |
| payment_id | S | GSI | Yes | Razorpay payment ID |
| gross_amount | N | — | Yes | Total payment |
| platform_fee | N | — | Yes | Platform fee |
| net_amount | N | — | Yes | Doctor payout |
| earning_month | S | — | Yes | YYYY-MM |
| status | S | — | Yes | `AVAILABLE` \| `PAID` |
| created_at | S | — | No | ISO 8601 |

**GSI:** `GSI_PaymentEarnings` (PK: payment_id). Created automatically on successful payment.

---

### DoctorPayoutsTable

Monthly withdrawal requests. PK = doctor_id, SK = payout_month (YYYY-MM).

| Attribute | Type | Key | Required | Description |
|-----------|------|-----|----------|-------------|
| PK | S | PK | Yes | doctor_id |
| SK | S | SK | Yes | payout_month (YYYY-MM) |
| payout_id | S | GSI | Yes | UUID |
| total_amount | N | — | Yes | Amount requested |
| status | S | — | Yes | `REQUESTED` \| `PROCESSING` \| `COMPLETED` \| `FAILED` |
| payout_method | S | — | Yes | `BANK` \| `UPI` |
| transaction_reference | S | — | No | Bank/UPI reference |
| requested_at | S | — | No | ISO 8601 |
| processed_at | S | — | No | ISO 8601 |

**GSI:** `GSI_PayoutId` (PK: payout_id). One payout per doctor per month; withdrawal allowed only after month end.

---

## Medical Records & Medilocker

### MedilockerDocuments

Document metadata for **every** patient document — uploaded by the patient or
by any hospital on the patient's behalf. PK = user_id, SK = created_at.
See `docs/DOCUMENT_UPLOAD_AND_ACCESS.md` for the full access-control model.

| Attribute | Type | Key | Required | Description |
|-----------|------|-----|----------|-------------|
| user_id | S | PK | Yes | Partition key — the patient, always |
| created_at | S | SK | Yes | Sort key (ISO 8601) |
| file_id | S | GSI | Yes | 8-char file ID |
| filename | S | — | Yes | Original filename |
| doc_type | S | — | No | Document type |
| s3_original_key | S | — | Yes | S3 key for original file (`Medilocker/Users/{user_id}/{file_id}/original.{ext}`) |
| s3_ocr_key | S | — | No | S3 key for OCR text |
| ocr_status | S | — | Yes | `PENDING` \| `COMPLETED` \| `FAILED` \| `SKIPPED` |
| structured_status | S | — | No | `PENDING` \| `COMPLETED` \| `FAILED` \| `SKIPPED` |
| document_category | S | — | No | e.g. `LAB_REPORT`, `PRESCRIPTION`, `SCAN_REPORT` |
| confidence | N | — | No | OCR confidence |
| file_metadata | M | — | No | file_type, file_size, etc. |
| upload_mode | S | — | No | `LIVE` for synchronous flows, `ASYNC` for background OCR uploads |
| source | S | — | Yes | `USER` (patient self-upload) \| `HOSPITAL` (uploaded by hospital staff) |
| hospital_id | S | GSI | No | Uploading hospital's ID — present **only** when `source = HOSPITAL` |
| updated_at | S | — | No | ISO 8601 |

**GSI:** `file_id-index` (PK: file_id). Direct file lookup.
**GSI:** `hospital-index` (PK: hospital_id, SK: created_at). Sparse — only
`source=HOSPITAL` rows have `hospital_id`, so this index is "every doc this
hospital uploaded," across all patients.

---

### HospitalFiles (deprecated)

Legacy raw hospital file metadata from the old `/hospital/upload`,
`/hospital/presign-upload`, `/hospital/confirm-upload` endpoints
(S3: `HospitalData/` or `hospital_uploads/`). Those endpoints and their
service code have been **removed** — hospital uploads now write to
`MedilockerDocuments` with `source=HOSPITAL` (see above). This table is kept
only so existing historical rows remain queryable; it receives no new writes.

| Attribute | Type | Key | Required | Description |
|-----------|------|-----|----------|-------------|
| hospital_id | S | PK | Yes | Partition key |
| file_id | S | SK | Yes | Sort key (8-char) |
| patient_id | S | — | Yes | Patient identifier |
| filename | S | — | Yes | Original filename |
| file_type | S | — | Yes | Extension (pdf, jpg, etc.) |
| s3_key | S | — | Yes | Full S3 object key |
| uploaded_at | S | — | Yes | ISO 8601 |
| upload_method | S | — | Yes | `API_UPLOAD` \| `PRESIGNED_UPLOAD` |
| file_size | N | — | No | Bytes |

---

## Chat & Records

### ChatHistory

User chat logs for AI assistant. Table name configurable via `DYNAMODB_TABLE` in chat lambda.

| Attribute | Type | Key | Required | Description |
|-----------|------|-----|----------|-------------|
| user_id | S | PK | Yes | Partition key (user_id or session_id for anonymous) |
| timestamp | N | SK | Yes | Sort key (Unix timestamp) |
| created_at | S | — | No | ISO 8601 |
| date_bucket | S | GSI | Yes | YYYY-MM-DD for date-range queries |
| user_message | S | — | Yes | User message |
| bot_message | S | — | Yes | Bot response |

**GSI:** `date-timestamp-index` (PK: date_bucket, SK: timestamp) — used for global history queries

---

## Relations (patient ↔ doctor ↔ hospital)

Three junction tables model the many-to-many links between patients, doctors and
hospitals. All follow the same shape: a composite key one way, and a GSI for the
reverse lookup.

### UserHospital

Patient ↔ hospital membership. ABHA signup links a user here.

| Attribute | Type | Key | Description |
|-----------|------|-----|-------------|
| hospital_id | S | PK | |
| user_id | S | SK | |

**GSI:** `GSI_UserHospitals` (PK: user_id, SK: hospital_id) — "which hospitals is this patient in?"

### DoctorHospital

Doctor ↔ hospital affiliation.

| Attribute | Type | Key | Description |
|-----------|------|-----|-------------|
| hospital_id | S | PK | |
| doctor_id | S | SK | |

**GSI:** `GSI_DoctorHospitals` (PK: doctor_id, SK: hospital_id) — "which hospitals does this doctor work at?"

### UserDoctorRelations *(legacy)*

⚠️ **Legacy — do not write new relations here.** Superseded by `UserDoctor`.
Still read by `BookingLambda` via `USER_DOCTOR_RELATIONS_TABLE`. The
`backfill_relations.py` script migrates rows from this table into `UserDoctor`
(see [SCRIPTS.md](SCRIPTS.md)).

| Attribute | Type | Key | Description |
|-----------|------|-----|-------------|
| relation_id | S | PK | |

**GSIs:** `GSI_UserRelations` (PK: user_id, SK: doctor_id), `GSI_DoctorRelations` (PK: doctor_id, SK: user_id)

---

## ABDM / ABHA

Tables backing the ABDM integration. See [ABDM_INTEGRATION.md](ABDM_INTEGRATION.md)
for how they fit together.

### AbhaAccounts

ABHA identity plus server-side ABDM tokens. Clients never hold an ABHA token —
it is resolved from here per request.

| Attribute | Type | Key | Description |
|-----------|------|-----|-------------|
| abha_number | S | PK | ABHA number |
| abha_address | S | GSI | ABHA address (`name@sbx`) |
| kokoro_user_id | S | GSI | Link back to the Kokoro user |

**GSIs:** `kokoro_user_id-index`, `abha_address-index`

> ⚠️ The `kokoro_user_id` attribute is **never written** by current code, so that
> GSI returns nothing. Known gap — [ABDM_INTEGRATION.md](ABDM_INTEGRATION.md) §2.

### HospitalAbdmConfig

Maps a Kokoro hospital to its ABDM HIP/HIU service ids. This is what makes one
ABDM client id serve many hospitals — the per-request `X-HIP-ID` is resolved here.

| Attribute | Type | Key | Description |
|-----------|------|-----|-------------|
| hospital_id | S | PK | |
| hip_id | S | GSI | ABDM HIP service id |
| hiu_id | S | GSI | ABDM HIU service id (set equal to `hip_id` at registration) |

**GSIs:** `hip_id-index`, `hiu_id-index`

### AbdmTransactions

Correlation log for asynchronous ABDM flows. Every request that expects a
callback is recorded here by `request_id`; the callback carries
`response.requestId` to match against. Inspect via `GET /abha/transactions`.

| Attribute | Type | Key | Description |
|-----------|------|-----|-------------|
| request_id | S | PK | Correlation id |
| hip_id | S | GSI | |
| abha_address | S | GSI | |
| created_at | S | GSI SK | |
| ttl | N | TTL | Expiry, `ABDM_TRANSACTION_TTL_DAYS` (90) |

**GSIs:** `hip_id-index` (SK: created_at), `abha_address-index` (SK: created_at)

### ConsentArtefacts

HIP-side consent artefacts granted by patients.

| Attribute | Type | Key | Description |
|-----------|------|-----|-------------|
| consent_id | S | PK | |
| ttl | N | TTL | |

### HiuConsentRequests

HIU-side consent requests Kokoro raises against other providers.

| Attribute | Type | Key | Description |
|-----------|------|-----|-------------|
| request_id | S | PK | |
| consent_request_id | S | GSI | Id assigned by the consent manager |
| hiu_id | S | GSI | |
| created_at | S | GSI SK | |
| ttl | N | TTL | |

**GSIs:** `consent_request_id-index`, `hiu_id-index` (SK: created_at)

### HiuDataRequests

HIU-side health-information requests and the ephemeral key material used to
decrypt incoming data.

| Attribute | Type | Key | Description |
|-----------|------|-----|-------------|
| request_id | S | PK | |
| transaction_id | S | GSI | Matches the transfer callback |
| ttl | N | TTL | |

**GSI:** `transaction_id-index`

> ⚠️ Stores the **ephemeral X25519 private key unwrapped**. Should be KMS-wrapped
> at rest — [ABDM_INTEGRATION.md](ABDM_INTEGRATION.md) §3.

---

## Other Storage (S3)

### Medilocker

User medical documents stored in S3. Metadata in DynamoDB `MedilockerDocuments`.

| Property | Value |
|----------|-------|
| Bucket | `kokoro-doctor` (configurable via `S3_BUCKET`) |
| Path | `Medilocker/Users/{user_id}/{file_id}/original.{ext}` |

Used for upload, list, download, prescription generation. See
[MEDILOCKER_STRUCTURE.md](../medilocker_lambda/docs/MEDILOCKER_STRUCTURE.md) and
[FETCH_FILES_ENDPOINT.md](../medilocker_lambda/docs/FETCH_FILES_ENDPOINT.md) for details.
