# 🩺 Kokoro Doctor – Healthcare Platform

**Kokoro Doctor** is a full-stack serverless platform that allows patients to:

- Book consultations with doctors
- Securely upload and manage medical records
- Subscribe to preferred doctors
- Chat using AI-powered assistants

Built using **FastAPI**, **AWS Lambda**, **DynamoDB**, **S3**, and **API Gateway**, the system is modular, scalable, and secure.

---

## 📚 Documentation

**Start here → [docs/README.md](docs/README.md)** — the full documentation index.

| If you are… | Read |
| ----------- | ---- |
| New to the codebase | [docs/ONBOARDING.md](docs/ONBOARDING.md) |
| Taking over the project | [docs/HANDOVER.md](docs/HANDOVER.md) |
| Deploying | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| Debugging production | [docs/OPERATIONS_RUNBOOK.md](docs/OPERATIONS_RUNBOOK.md) |
| Looking for an endpoint | [docs/LAMBDA_FUNCTIONS.md](docs/LAMBDA_FUNCTIONS.md) |

> ⚠️ **New maintainers:** the real `template.yaml` is **not** in this repository —
> it contains live credentials and is gitignored. Deploy from the sanitised
> [`template.example.yaml`](template.example.yaml). See
> [docs/HANDOVER.md](docs/HANDOVER.md) §2 before your first deploy.

---

## 🌐 Live Domain

**Frontend & API Gateway**: [https://kokoro.doctor](https://kokoro.doctor)

---

## 🧱 Tech Stack

| Layer         | Tools / Services                                 |
| ------------- | ------------------------------------------------ |
| Backend       | FastAPI + AWS Lambda (Python 3.11)               |
| Frontend      | React (deployed on EC2 with Spot instance logic) |
| Infra as Code | AWS SAM (`template.yaml`)                        |
| Database      | DynamoDB                                         |
| Storage       | AWS S3 (file storage, documents)                 |
| API Gateway   | AWS API Gateway (with proxy integrations)        |
| Payments      | Razorpay                                         |

---

## 📁 Backend folder layout (Medilocker, Chat, Hospitals)

SAM and docs live at the `backend/` root; each deployable unit is a `*_lambda` folder with `app/` (FastAPI).

```
backend/
├── readme.md              # This file — overview and entry points
├── docs/                  # All documentation — start at docs/README.md
├── template.example.yaml  # AWS SAM (sanitised, committed) — deploy from this
├── template.yaml          # Real SAM template — gitignored, holds live secrets
├── samconfig.toml         # SAM CLI deploy defaults (stack, region, …)
├── scripts/               # Operational / deploy helpers (see docs/SCRIPTS.md)
├── medilocker_lambda/
│   ├── requirements.txt
│   ├── docs/
│   └── app/
│       ├── main.py
│       ├── config.py
│       ├── logger.py
│       ├── models/
│       ├── routers/       # medilocker_router
│       ├── services/      # OCR, extraction, claims, prescriptions, …
│       │   └── prompts/
│       └── utils/
├── chat_lambda/
│   ├── requirements.txt
│   ├── CHAT_LAMBDA_FLOW.md
│   └── app/
│       ├── main.py
│       ├── config.py
│       ├── logger.py
│       ├── models/
│       ├── routers/
│       ├── services/
│       └── utils/
└── hospitals_lambda/
    ├── requirements.txt
    └── app/
        ├── main.py
        ├── config.py
        ├── logger.py
        ├── auth/
        ├── models/
        ├── routers/
        ├── services/
        └── utils/
```

Other Lambdas in this repo (e.g. `auth_lambda`, `booking_lambda`, `payment_lambda`) follow the same pattern and are defined in `template.yaml`.

---

## 📦 Services & Endpoints

Each FastAPI application is deployed independently behind API Gateway. CORS is pre-configured for `https://kokoro.doctor` and `http://localhost:8081`.

### 1. Auth Service (`auth_lambda`)

Base path: `/auth`

- `POST /auth/user/signup` – Register a new patient account (email verification token issued and mailed)
- `POST /auth/user/login` – Validate credentials and return a short-lived session token
- `POST /auth/doctor/signup` – Register a new doctor account and trigger verification mail
- `POST /auth/doctor/login` – Authenticate doctor credentials
- `POST /auth/google` – Sign in via Google OAuth token (auto-creates account if needed)
- `POST /auth/session/initiate` – Create anonymous session and return 7-day session identifier
- `POST /auth/verify` – Verify email using `email` and `token` query/body parameters
- `POST /auth/request-password-reset` – Send password reset e-mail and persist token (15 min TTL)
- `POST /auth/reset-password` – Reset password using reset token
- `POST /auth/send-mobile-otp` – Send SMS OTP for phone verification
- `POST /auth/verify-mobile-otp` – Confirm OTP and mark phone as verified

### 2. Doctor Service (`doctorsService_lambda`)

Base path: `/doctorsService`

- `POST /doctorsService/updateProfile` – Complete or update doctor onboarding metadata and documents
- `POST /doctorsService/fetchDoctors` – Retrieve doctors, optionally filtered by category, with signed media URLs
- `POST /doctorsService/setSlots` – Bulk-create availability slots for a specific weekday
- `POST /doctorsService/updateSlot` – Toggle availability for a single slot

> **Note:** Subscriptions are managed via the Booking Lambda (`/booking/subscriptions`) and are created automatically after successful payment. See Subscription System documentation.

### 3. Booking Service (`booking_lambda`)

Base path: `/booking`

- `POST /booking/bookings` – Book a slot atomically (enforces capacity & unique reservations via DynamoDB)
- `DELETE /booking/bookings/{booking_id}` – Cancel an existing booking and free the slot
- `GET /booking/doctors/{doctor_id}/availability?date=YYYY-MM-DD` – List available slots for a doctor on a specific date
- `GET /booking/doctors/{doctor_id}/bookings?date=YYYY-MM-DD` – Fetch bookings for a doctor
- `GET /booking/users/{user_id}/bookings?type=upcoming|past` – Fetch bookings for a user
- `GET /booking/doctors/{doctor_id}/calendar?days=N` – Get unified calendar for a doctor

This service also owns subscription plans and user subscriptions (`/booking/plans`,
`/booking/subscriptions`). Full route list: [docs/LAMBDA_FUNCTIONS.md](docs/LAMBDA_FUNCTIONS.md).

> ⏱️ Slots are 30 minutes, bookable up to 15 days ahead, capped at five patients per slot.

### 4. Medilocker Service (`medilocker_lambda`)

Base paths: `/medilocker`, `/hospital`

- `POST /medilocker/upload` – Upload one or more medical files to S3
- `GET /medilocker/users/{user_id}/files` – List stored files (optional `?category=`)
- `GET /medilocker/users/{user_id}/files/{file_id}/download` – Pre-signed download URL
- `DELETE /medilocker/users/{user_id}/files/{file_id}` – Remove a stored file
- `POST /medilocker/users/{user_id}/prescription` – AI-generated prescription from stored documents
- `POST /medilocker/insurance/analyze`, `POST /medilocker/discharge/analyze` – OCR + LLM document analysis
- `POST /medilocker/upload/async` – Enqueue for background OCR (`OCRWorkerLambda`)

Also hosts the `/hospital/*` raw-ingestion routes (auth: `x-hospital-api-key`).

### 5. Chat Service (`chat_lambda`)

Base path: `/chat`

- `POST /chat/send` – Send a user or session message; attempts RAG answer first, falls back to LLM, persists transcript
- `GET /chat/history/user`, `GET /chat/history/global` – Chat history queries

### 6. Payment Lambda (`payment_lambda`)

Base path: `/process-payment`

- `POST /process-payment/payment-link` – Create a Razorpay payment link for a subscription plan
  - Body: `{ "plan_id": "string", "user_id": "string", "doctor_id": "string" }`
- `POST /process-payment/webhook` – Razorpay webhook handler; on success creates the subscription and writes a `DoctorEarningsLedger` entry

### 7. Other services

`userService_lambda` (`/users`), `doctorsService_lambda` (`/doctorsService`),
`hospitals_lambda` (`/hospitals`), `doctor_payouts_lambda` (`/payouts`),
`abha_lambda` (`/abha`, `/api/v3`) and the SQS-triggered `ocr_worker_lambda`.

**Full, verified route list for all eleven services:
[docs/LAMBDA_FUNCTIONS.md](docs/LAMBDA_FUNCTIONS.md).**

---

## ⚙️ Environment Configuration

Each Lambda reads its configuration from environment variables via its own
`app/config.py`. In production these are set by the SAM template; for local runs
each service needs its own (gitignored) `.env`.

**Complete per-service inventory, including which values are secrets and which
must match across services: [docs/ENVIRONMENT_VARIABLES.md](docs/ENVIRONMENT_VARIABLES.md).**

> `JWT_SECRET` must be identical in `auth_lambda`, `abha_lambda` and
> `hospitals_lambda`, or tokens issued by one will not validate in the others.

---

## 🗃️ DynamoDB Tables

26 tables in total. The main ones:

| Table                     | Purpose                                                                   |
| ------------------------- | ------------------------------------------------------------------------- |
| `Users`                   | User login data, subscriptions                                            |
| `Doctors`                 | Doctor data and onboarding status                                         |
| `DoctorAvailabilityTable` | Stores time slots per doctor per date (PK: doctor_id, SK: date#slot_time) |
| `AppointmentsTable`       | Stores user bookings (PK/SK + GSI)                                        |
| `ChatHistory`             | Stores user chat logs with timestamps                                     |
| `PaymentsTable`           | Stores Razorpay payment info                                              |
| `MedilockerDocuments`     | Medical document metadata (S3 objects + OCR results)                      |
| `Hospitals`, `UserHospital`, `DoctorHospital`, `UserDoctor` | Hospital accounts and the patient/doctor/hospital relation junctions |
| `SubscriptionPlans`, `UserDoctorSubscriptions` | Subscription plans and entitlements                    |
| `DoctorEarningsLedger`, `DoctorPayoutsTable` | Doctor earnings and payouts                              |
| `AbhaAccounts`, `HospitalAbdmConfig`, `AbdmTransactions`, `ConsentArtefacts`, `HiuConsentRequests`, `HiuDataRequests` | ABDM / ABHA integration |

**Full schemas, keys and GSIs for all 26: [docs/DATABASE_TABLES.md](docs/DATABASE_TABLES.md).**

### 🧠 Bookings Table Schema

| Field        | Format                               |
| ------------ | ------------------------------------ |
| `PK`         | `doctor_id`                          |
| `SK`         | `YYYY-MM-DD#HH:MM` (date#start_time) |
| `doctor_id`  | Doctor identifier                    |
| `date`       | Date in YYYY-MM-DD format            |
| `start_time` | Time in HH:MM format                 |
| `user_id`    | User identifier                      |
| `booking_id` | Unique booking identifier            |
| `created_at` | ISO timestamp                        |
| `status`     | Optional booking status              |

**GSI_UserBookings:**

- `GSI1PK` (Partition Key): `user_id`
- `GSI1SK` (Sort Key): `SK` (contains `date#start_time`)

---

## ☁️ S3 Buckets

| Bucket Name     | Usage                                                |
| --------------- | ---------------------------------------------------- |
| `kokoro-doctor` | Single bucket with two folders:                      |
|                 | - `Medilocker/` - User-uploaded medical files        |
|                 | - `DoctorDocuments/` - Doctor registration documents |

---

## 🚀 Deployment (AWS SAM)

Stack `sam-app` in `ap-south-1`, API Gateway stage `prod`.

```bash
sam build --template template.example.yaml
```

```bash
sam deploy --template template.example.yaml --capabilities CAPABILITY_NAMED_IAM
```

Secrets are CloudFormation parameters and must be supplied at deploy time.
**Read [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) first** — there is no staging
environment, and every deploy goes to production.
