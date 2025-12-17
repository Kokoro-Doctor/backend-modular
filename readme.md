# 🩺 Kokoro Doctor – Healthcare Platform

**Kokoro Doctor** is a full-stack serverless platform that allows patients to:

- Book consultations with doctors
- Securely upload and manage medical records
- Subscribe to preferred doctors
- Chat using AI-powered assistants

Built using **FastAPI**, **AWS Lambda**, **DynamoDB**, **S3**, and **API Gateway**, the system is modular, scalable, and secure.

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

### 3. Appointment Service (`appointmentService_lambda`)

Base path: `/appointmentService`

- `POST /appointmentService/bookings` – Book a slot atomically (enforces capacity & unique reservations via DynamoDB)
- `DELETE /appointmentService/bookings/{booking_id}` – Cancel an existing booking and free the slot
- `GET /appointmentService/doctors/{doctor_id}/availability?date=YYYY-MM-DD` – List available slots for a doctor on a specific date
- `GET /appointmentService/doctors/{doctor_id}/bookings?date=YYYY-MM-DD` – Fetch bookings for a doctor
- `GET /appointmentService/users/{user_id}/bookings?type=upcoming|past` – Fetch bookings for a user
- `GET /appointmentService/doctors/{doctor_id}/calendar?days=N` – Get unified calendar for a doctor

> ⏱️ Slots are 30 minutes, bookable up to 15 days ahead, capped at five patients per slot.

### 4. Medilocker Service (`medilocker_lambda`)

Base path: `/medilocker`

- `POST /medilocker/upload` – Upload one or more encrypted medical files to S3
- `POST /medilocker/fetch` – List stored files with metadata and signed download links
- `POST /medilocker/download` – Generate a single-file pre-signed download URL
- `POST /medilocker/delete` – Remove a stored file
- `POST /medilocker/generate-prescription` – Summarise selected documents and symptoms into an AI-generated prescription

### 5. Chat Service (`chat_lambda`)

Base path: `/chat`

- `POST /chat` – Send a user or session message; attempts RAG answer first, falls back to LLM, persists transcript

### 6. Payment Lambda (`payment`)

- `POST /payment` (API Gateway integration target)
  - With `{ "amount": 999 }` → Creates a Razorpay payment link and returns `short_url`
  - With `{ "payment_id": "pay_..." }` → Verifies payment, stores record in DynamoDB, and returns invoice link (when captured)

> The function also responds to CORS `OPTIONS` requests automatically.

---

## ⚙️ Environment Configuration

Set the following variables for each Lambda before deployment (SAM templates wire them in production; required for local runs or tests):

- **Auth Service**: `USERS_TABLE`, `DOCTORS_TABLE`, `AUTH_TOKENS_TABLE`, `SESSIONS_TABLE` (optional), `BREVO_SMTP_USER`, `BREVO_SMTP_KEY`, `BREVO_SMTP_SERVER`, `BREVO_SMTP_PORT`, `SMS_AWS_REGION` (default `ap-south-1`), `SMS_COUNTRY_CODE` (default `+91`). Optional rate-limit overrides: `EMAIL_VERIFICATION_RATE_LIMIT_MAX_ATTEMPTS`, `EMAIL_VERIFICATION_RATE_LIMIT_WINDOW_SECONDS`, `MOBILE_OTP_RATE_LIMIT_MAX_ATTEMPTS`, `MOBILE_OTP_RATE_LIMIT_WINDOW_SECONDS`, `PASSWORD_RESET_EMAIL_RATE_LIMIT_MAX_ATTEMPTS`, `PASSWORD_RESET_EMAIL_RATE_LIMIT_WINDOW_SECONDS`, `PASSWORD_RESET_SMS_RATE_LIMIT_MAX_ATTEMPTS`, `PASSWORD_RESET_SMS_RATE_LIMIT_WINDOW_SECONDS`.
- **Doctor Service**: `DOCTORS_TABLE`, `USERS_TABLE`, `S3_BUCKET` (default `kokoro-doctor`, uses `DoctorDocuments/` folder)
- **Booking Service**: `AWS_REGION` (default `ap-south-1`)
- **Medilocker Service**: `S3_BUCKET` (default `kokoro-doctor`, uses `Medilocker/` folder), `OPENAI_API_KEY`
- **Chat Service**: `DYNAMODB_TABLE`, `OPENAI_API_KEY`, `RAG_SERVER_URL`
- **Payment Lambda**: `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `DYNAMODB_TABLE_NAME`

---

## 🗃️ DynamoDB Tables

| Table                     | Purpose                                                                   |
| ------------------------- | ------------------------------------------------------------------------- |
| `Users`                   | User login data, subscriptions                                            |
| `Doctors`                 | Doctor data and onboarding status                                         |
| `DoctorAvailabilityTable` | Stores time slots per doctor per date (PK: doctor_id, SK: date#slot_time) |
| `AppointmentsTable`       | Stores user bookings (PK/SK + GSI)                                        |
| `ChatHistory`             | Stores user chat logs with timestamps                                     |
| `PaymentsTable`           | Stores Razorpay payment info                                              |

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

### Build

```bash
sam build
```
