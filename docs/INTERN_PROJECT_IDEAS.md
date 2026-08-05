# Intern Project Ideas — Independent Backend Features

This document lists new backend features that can be owned **solely by an intern**. Each feature is self-contained, fits the existing architecture, and has minimal overlap with other teams.

---

## Backend Architecture (Quick Reference)

| Component | Tech |
|-----------|------|
| **Framework** | AWS SAM, FastAPI + Mangum, Python 3.11 |
| **Database** | DynamoDB |
| **Storage** | S3 (`kokoro-doctor` bucket) |
| **Email** | Brevo SMTP |
| **SMS** | AWS SNS |
| **Auth** | JWT, OTP, Google OAuth |

**Pattern for new features:** Create a new Lambda (e.g. `feedback_lambda/`) with FastAPI + Mangum, add it to `template.yaml`, and wire a new API path (e.g. `/feedback/{proxy+}`).

---

## Project 1: Doctor Ratings & Feedback (Recommended)

**Scope:** Allow users to rate and review doctors after appointments.

**Why it's independent:**
- New DynamoDB table only
- New Lambda only
- Reads from `AppointmentsTable` and `Doctors` (no writes to them)
- No changes to existing lambdas

**Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| POST | `/feedback/rate` | Submit rating (1–5) + optional text after appointment |
| GET | `/feedback/doctor/{doctor_id}` | Get aggregated ratings and recent reviews (paginated) |
| GET | `/feedback/user/{user_id}` | Get user's past ratings |

**Data model:**
- **DoctorRatingsTable** (DynamoDB)
  - PK: `doctor_id`, SK: `rating_id` (uuid)
  - Attributes: `user_id`, `appointment_id`, `rating` (1–5), `review_text`, `created_at`
  - GSI: `user_id` (PK) + `created_at` (SK) for user's ratings

**Validation:**
- User can rate only if they have a completed appointment with that doctor
- One rating per appointment
- Auth: require `Authorization: Bearer <jwt>` or `x-user-id` header

**Reference:** `userService_lambda/` for structure, `booking_lambda/` for appointment queries.

**Complexity:** Medium | **Estimate:** 1–2 weeks

---

## Project 2: Health Records Export (PDF/ZIP)

**Scope:** Export a user's Medilocker documents as a single PDF or ZIP.

**Why it's independent:**
- Uses existing `MedilockerDocuments` table and S3 (`Medilocker/Users/`)
- Can live as new routes in `medilocker_lambda` OR a separate Lambda
- Read-only access to existing data

**Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| POST | `/medilocker/export/pdf` | Generate PDF of all documents for `user_id` |
| POST | `/medilocker/export/zip` | Generate ZIP of raw files for `user_id` |

**Flow:**
1. Auth: verify user owns the data
2. Query `MedilockerDocuments` by `user_id`
3. Fetch files from S3
4. Use `reportlab` or `fpdf2` for PDF; `zipfile` for ZIP
5. Return as download (stream response) or upload to S3 and return presigned URL

**Dependencies:** Add `reportlab` or `fpdf2` to `medilocker_lambda/requirements.txt`.

**Reference:** `medilocker_lambda/app/services/document_db_service.py`, `file_service.py`.

**Complexity:** Medium | **Estimate:** 1–2 weeks

---

## Project 3: Referral / Invite System

**Scope:** Referral codes for users to invite others; track referrals.

**Why it's independent:**
- New DynamoDB table
- New Lambda
- Optional: small reward (e.g. discount code) — can be Phase 2

**Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/referral/code` | Get or create user's referral code |
| GET | `/referral/stats` | Get count of successful referrals |
| POST | `/referral/apply` | Apply referral code at signup (called from Auth flow) |

**Data model:**
- **ReferralsTable**
  - PK: `referrer_id` (user_id), SK: `referred_id` (user_id)
  - Attributes: `referral_code`, `created_at`, `status` (pending/completed)

**Flow:**
- Each user gets a unique code (e.g. `KOKORO-{short_id}`)
- When new user signs up with `referral_code`, insert into ReferralsTable
- Stats: count where `referrer_id` = current user

**Reference:** `auth_lambda` for signup flow (minimal integration point).

**Complexity:** Low–Medium | **Estimate:** 1 week

---

## Project 4: Usage Analytics (Admin-Only)

**Scope:** Read-only analytics endpoints for internal dashboards.

**Why it's independent:**
- Read-only DynamoDB scans/queries
- New Lambda, admin-only via `x-admin-key` header
- No writes to existing tables

**Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/analytics/appointments` | Count appointments by date range, by doctor |
| GET | `/analytics/subscriptions` | Active subscriptions, revenue |
| GET | `/analytics/chat` | Chat sessions count |
| GET | `/analytics/medilocker` | Upload counts by user |

**Auth:** Require `x-admin-key: <ADMIN_KEY>` (reuse from Auth/Booking).

**Implementation:**
- Query `AppointmentsTable`, `UserDoctorSubscriptions`, `ChatHistory`, `MedilockerDocuments`
- Aggregate by date (use `created_at` or `booking_date`)
- Return JSON summaries (counts, simple breakdowns)

**Reference:** `booking_lambda` (admin endpoints), `template.yaml` (ADMIN_KEY).

**Complexity:** Low | **Estimate:** 3–5 days

---

## Project 5: Appointment Reminders (Scheduled Lambda)

**Scope:** Send SMS/email reminders before appointments.

**Why it's independent:**
- New Lambda triggered by EventBridge (cron)
- Reads `AppointmentsTable`, sends via Brevo/SNS
- No changes to Booking or Auth

**Flow:**
1. EventBridge rule: run every 15 min (or hourly)
2. Lambda: query appointments where `appointment_date` = tomorrow (or in 24h)
3. For each: send email via Brevo (reuse SMTP config from Auth/Payment)
4. Optional: SMS via SNS for users with verified phone

**Data:** Reuse `AppointmentsTable`; may need `reminder_sent` flag to avoid duplicates.

**Reference:** `auth_lambda` (Brevo), `payment_lambda` (email templates).

**Complexity:** Medium | **Estimate:** 1 week

---

## Project 6: Notification Preferences

**Scope:** Let users configure which notifications they receive (email, SMS, push).

**Why it's independent:**
- New DynamoDB table
- New Lambda
- Foundation for future push notifications

**Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/notifications/preferences` | Get user preferences |
| PUT | `/notifications/preferences` | Update preferences |

**Data model:**
- **NotificationPreferencesTable**
  - PK: `user_id`
  - Attributes: `email_appointments`, `email_payments`, `sms_appointments`, `sms_otp`, etc. (booleans)

**Reference:** `userService_lambda` for simple CRUD pattern.

**Complexity:** Low | **Estimate:** 3–5 days

---

## Project 7: Audit Log (Action Logging)

**Scope:** Log important actions (auth, payments, bookings) for compliance/debugging.

**Why it's independent:**
- New DynamoDB table
- New Lambda with single `POST /audit` endpoint
- Other lambdas call it via Lambda invoke or HTTP (fire-and-forget)

**Data model:**
- **AuditLogTable**
  - PK: `entity_type` (e.g. `payment`, `booking`, `auth`)
  - SK: `timestamp` (ISO) or `log_id`
  - Attributes: `action`, `user_id`, `details` (JSON), `ip`, `user_agent`

**Integration:** Other lambdas can invoke AuditLambda asynchronously or POST to an internal URL. Start simple: manual calls from 1–2 lambdas (e.g. payment, booking).

**Complexity:** Medium | **Estimate:** 1–2 weeks

---

## Summary: Recommended Order for Intern

| Priority | Project | Complexity | Value | Dependencies |
|----------|---------|------------|-------|--------------|
| 1 | Doctor Ratings & Feedback | Medium | High | AppointmentsTable, Doctors |
| 2 | Referral System | Low–Medium | High | Auth (light touch) |
| 3 | Health Records Export | Medium | High | Medilocker, S3 |
| 4 | Usage Analytics | Low | Medium | All tables (read-only) |
| 5 | Notification Preferences | Low | Medium | None |
| 6 | Appointment Reminders | Medium | High | EventBridge, Brevo/SNS |
| 7 | Audit Log | Medium | Medium | Lambda invoke pattern |

---

## How to Add a New Lambda (Checklist)

1. Create folder: `backend/<feature>_lambda/`
2. Structure:
   ```
   <feature>_lambda/
   ├── app/
   │   ├── __init__.py
   │   ├── main.py          # FastAPI app + Mangum handler
   │   ├── routers/
   │   │   └── <feature>_router.py
   │   ├── services/        # optional
   │   └── logger.py
   ├── requirements.txt
   └── template.yaml        # NO - add to root template.yaml
   ```
3. In `backend/template.yaml`:
   - Add DynamoDB table(s) if needed
   - Add Lambda resource (CodeUri, Handler, Environment)
   - Add API path (e.g. `/feedback/{proxy+}`)
   - Add Lambda permission for API Gateway
4. Add IAM permissions for new tables in `LambdaExecutionRole`
5. Update the relevant file under `docs/api-test-bodies/` (and `README.md` notes/changelog if needed) when endpoints change

---

## Reference Files

- **Lambda structure:** `userService_lambda/`, `doctor_payouts_lambda/`
- **DynamoDB patterns:** `booking_lambda/`, `medilocker_lambda/`
- **Auth patterns:** `auth_lambda/docs/AUTH_FLOW_DOCUMENTATION.md`
- **API test bodies:** `docs/api-test-bodies/README.md` (per-Lambda `*.md` in that folder; entry point `docs/API_TEST_BODIES.md`)
- **Medilocker services:** `medilocker_lambda/docs/SERVICES_REFERENCE.md`
