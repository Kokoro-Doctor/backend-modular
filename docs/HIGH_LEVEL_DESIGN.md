# Kokoro-Doctor — High-Level Design (HLD)

> **Last updated:** March 2025

## 1. Executive Summary

**Kokoro-Doctor** is a healthcare platform connecting patients with doctors via subscriptions, medical records (Medilocker), AI chat, and hospital data ingestion. It comprises a React Native/Expo frontend and an AWS serverless backend (Lambda + API Gateway).

---

## 2. System Overview

### 2.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              KOKORO-DOCTOR PLATFORM                               │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │  FRONTEND (React Native + Expo)                                          │   │
│   │  https://kokoro.doctor                                                   │   │
│   │  • Patient: Medilocker, Booking, Chat, Hospitals                         │   │
│   │  • Doctor: Dashboard, Subscribers, Prescription, Payouts                 │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                        │                                         │
│                                        ▼                                         │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │  API GATEWAY (REST, prod stage)                                          │   │
│   │  CORS: kokoro.doctor, localhost:8081                                     │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                        │                                         │
│                                        ▼                                         │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │  LAMBDA LAYER (Python 3.11, FastAPI + Mangum)                            │   │
│   │  Auth | User | Doctors | Hospitals | Booking | Chat | Payment | Payouts   │   │
│   │  Medilocker (incl. Hospital Raw Ingestion)                               │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                        │                                         │
│         ┌──────────────────────────────┼──────────────────────────────┐          │
│         ▼                              ▼                              ▼          │
│   ┌───────────┐                 ┌───────────┐                 ┌───────────────┐  │
│   │ DynamoDB  │                 │    S3     │                 │   EXTERNAL    │  │
│   │ 17 tables │                 │ kokoro-  │                 │ Razorpay     │  │
│   │           │                 │ doctor   │                 │ Brevo, OpenAI │  │
│   │           │                 │          │                 │ Groq, RAG     │  │
│   │           │                 │          │                 │ Textract      │  │
│   └───────────┘                 └───────────┘                 └───────────────┘  │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Technology Stack

| Layer | Technology |
|-------|------------|
| **Frontend** | React Native, Expo, React Navigation |
| **Backend** | Python 3.11, FastAPI, Mangum |
| **Infrastructure** | AWS SAM, Lambda, API Gateway |
| **Database** | DynamoDB (PAY_PER_REQUEST) |
| **Storage** | S3 (`kokoro-doctor` bucket) |
| **Region** | `ap-south-1` (Mumbai) |

---

## 3. Domain Model

### 3.1 Actors

| Actor | Description |
|-------|-------------|
| **Patient (User)** | Subscribes to doctors, manages Medilocker, uses AI chat |
| **Doctor** | Manages profile, availability, subscribers, prescriptions, payouts; links to hospitals |
| **Hospital** | Uploads raw medical data via API (API key auth); managed by admin |
| **Admin** | Account deletion, payout status updates, hospital CRUD |

### 3.2 Core Entities

- **User** — Patient profile (user_id, name, email, phone)
- **Doctor** — Doctor profile (doctor_id, specialization, fees, hospital_id, documents)
- **Hospital** — Admin-managed hospital (hospital_id, name, address, city, state, contact)
- **Subscription Plan** — Plan definition (price, validity, appointments)
- **User–Doctor Subscription** — Active subscription linking user ↔ doctor
- **Appointment** — Booking slot (doctor, date, time, user)
- **Medilocker Document** — Medical file (S3 + DynamoDB metadata)
- **Hospital Raw File** — Raw hospital data (no OCR/AI)

---

## 4. Backend Services (Lambda Functions)

### 4.1 Service Map

| Lambda | Base Path | Responsibility |
|--------|-----------|----------------|
| **AuthLambda** | `/auth` | Signup, login, OTP, Google OAuth, session initiation |
| **UserServiceLambda** | `/users` | User profile retrieval |
| **HospitalsLambda** | `/hospitals` | Admin-managed hospital CRUD; doctors link via hospital_id |
| **DoctorsServiceLambda** | `/doctorsService` | Doctor profiles, documents, availability, hospital filter |
| **BookingLambda** | `/booking` | Appointments, subscription plans, user subscriptions |
| **ProcessPaymentLambda** | `/process-payment` | Razorpay links, webhooks, subscription creation |
| **DoctorPayoutsLambda** | `/payouts` | Earnings summary, payout requests, admin processing |
| **MediLockerLambda** | `/medilocker`, `/hospital` | Medical documents, prescriptions, hospital raw ingestion |
| **ChatLambda** | `/chat` | AI chat (RAG + LLM fallback) |

### 4.2 API Gateway Routing

All Lambdas are exposed via a single REST API:

```
https://j26e2xwzm1.execute-api.ap-south-1.amazonaws.com/prod/{path}
```

Paths are routed by prefix (e.g. `/auth/*`, `/hospitals/*`, `/booking/*`, `/chat`, etc.).

---

## 5. Data Layer

### 5.1 DynamoDB Tables

| Table | PK | SK / GSIs | Purpose |
|-------|----|-----------|---------|
| **Users** | user_id | email-index, phone-index | Patient profiles |
| **Doctors** | doctor_id | email-index, phone-index | Doctor profiles (incl. hospital_id) |
| **Hospitals** | hospital_id | — | Admin-managed hospitals |
| **AuthTable** | phoneNumber | email-index | Auth identity store |
| **AuthTokensTable** | token_id | purpose, email-index, phone-index | OTP tokens (TTL) |
| **SessionsTable** | session_id | — | Anonymous sessions (TTL) |
| **DoctorAvailabilityTable** | PK (doctor_id) | SK (date#slot_time) | Availability slots |
| **AppointmentsTable** | PK (doctor_id) | SK (date#time), user_id GSI, booking_id GSI | Bookings |
| **SubscriptionPlans** | plan_id | doctor_id GSI | Plan definitions |
| **UserDoctorSubscriptions** | subscription_id | user_id, doctor_id, payment_id GSIs | User–doctor subscriptions |
| **PaymentsTable** | payment_id | — | Razorpay payments |
| **DoctorEarningsLedger** | doctor_id | YYYY-MM#payment_id | Earnings ledger |
| **DoctorPayoutsTable** | doctor_id | payout_month | Payout requests |
| **ChatHistory** | user_id | timestamp, date_bucket GSI | Chat logs |
| **MedilockerDocuments** | user_id | created_at, file_id GSI | Medilocker metadata |
| **HospitalFiles** | hospital_id | file_id | Hospital raw ingestion metadata |

### 5.2 S3 Structure

| Prefix | Purpose |
|--------|---------|
| `Medilocker/Users/{user_id}/{file_id}/` | Patient medical files |
| `DoctorDocuments/` | Doctor registration documents |
| `HospitalData/{hospital_id}/{patient_id}/{file_id}/` | Hospital raw files (API upload) |
| `hospital_uploads/{hospital_id}/{patient_id}/{file_id}/` | Hospital raw files (presigned upload) |

---

## 6. Key Flows

### 6.1 Authentication Flow

```
┌─────────┐     POST /auth/login        ┌─────────┐     Lookup AuthTable
│ Frontend │ ──────────────────────────>│ Auth    │ ──────────────────────> DynamoDB
└─────────┘     { identifier }          │ Lambda  │
     │                                  └────┬────┘
     │                                       │
     │<── { role, otp_required: true } ──────┘
     │
     │     POST /auth/request-otp
     │ ─────────────────────────────> Brevo (email/SMS)
     │
     │     POST /auth/login
     │ ─────────────────────────────> Validate OTP → JWT
     │     { identifier, otp }
     │
     │<── { access_token, user_id } ──────────
```

- **Normal flow:** Discovery → OTP request → OTP validation → JWT.
- **Experimental flow:** Phone-only, no OTP, direct JWT.

### 6.2 Hospital Management Flow

```
Admin → POST /hospitals (x-admin-api-key) → Create hospital
     → PUT /hospitals/{id} → Update
     → PUT /hospitals/{id}/disable → Soft delete

Doctor → updateProfile with hospital_id → Validated against Hospitals table
      → GET /doctorsService/doctors?hospital_id=HOSP_xxx → Filter by hospital
```

### 6.3 Doctor–User Connection Flow

```
Doctor Signup → User Signup → User Subscribes (Razorpay) → Subscription Created
                                    │
                                    └──> Doctor views subscribers via GET /booking/doctors/{id}/subscribers
```

### 6.4 Medilocker Flow

```
Upload → S3 + DynamoDB → Textract OCR → ocr.txt in S3
                              │
                              └──> Groq extraction → structured_data in DynamoDB
                                                              │
                                                              └──> Prescription: Groq synthesis
```

### 6.5 Chat Flow

```
POST /chat → RAG Server (primary) ──> Response
                │
                └──> OpenAI LLM (fallback if RAG fails)
```

### 6.6 Payment Flow

```
POST /process-payment/payment-link → Razorpay link
         │
         └──> User pays → Razorpay webhook (payment.captured)
                              │
                              └──> BookingLambda: create subscription
                              └──> DoctorEarningsLedger: earnings record
```

### 6.7 Hospital Raw Ingestion Flow

**Method 1: Direct API Upload**

```
POST /hospital/upload (x-hospital-api-key, multipart/form-data)
  → S3: HospitalData/{hospital_id}/{patient_id}/{file_id}/original.{ext}
  → DynamoDB: HospitalFiles
```

**Method 2: Presigned Batch Upload**

```
POST /hospital/presign-upload { hospital_id, patient_id, files: [{ filename }] }
  → Returns presigned URLs per file

Hospital PUTs each file to presigned URL

POST /hospital/confirm-upload { hospital_id, patient_id, files: [{ file_id, filename, file_size }] }
  → Saves metadata to DynamoDB HospitalFiles
```

No OCR, Textract, or AI processing — storage only.

---

## 7. External Integrations

| Service | Purpose |
|---------|---------|
| **Razorpay** | Payment links, webhooks, subscriptions |
| **Brevo** | Email OTP, verification, password reset |
| **OpenAI** | Chat fallback (gpt-3.5-turbo) |
| **Groq** | Medilocker extraction, prescription synthesis |
| **AWS Textract** | OCR for medical documents |
| **RAG Server** | Primary chat retrieval (external endpoint) |
| **Google OAuth** | Sign-in via Google |
| **Mixpanel** | Analytics and user identification |

---

## 8. Security Model

| Mechanism | Scope |
|-----------|-------|
| **JWT** | User/doctor API auth (Bearer token) |
| **x-admin-api-key** | Hospital CRUD (create, update, disable) |
| **x-hospital-api-key** | Hospital raw data ingestion |
| **Razorpay Webhook** | Signature verification |
| **CORS** | `kokoro.doctor`, `localhost:8081` |

---

## 9. Deployment

| Component | Deployment |
|-----------|------------|
| **Backend** | AWS SAM (`template.yaml`) → Lambda + API Gateway |
| **Frontend** | EC2 Spot instance (per README), served at `https://kokoro.doctor` |
| **IaC** | AWS SAM only (no Terraform/CDK) |

---

## 10. API Endpoints Summary

| Path | Lambda | Key Endpoints |
|------|--------|---------------|
| `/auth/*` | AuthLambda | login, signup, OTP, Google, session |
| `/users/*` | UserServiceLambda | GET user profile |
| `/hospitals` | HospitalsLambda | CRUD (admin), list active |
| `/doctorsService/*` | DoctorsServiceLambda | updateProfile, doctors (filter by hospital_id), slots |
| `/booking/*` | BookingLambda | bookings, plans, subscriptions |
| `/process-payment/*` | ProcessPaymentLambda | payment-link, webhook |
| `/payouts/*` | DoctorPayoutsLambda | earnings, request, history, admin |
| `/medilocker/*` | MediLockerLambda | upload, files, prescription, clinical-query |
| `/hospital/*` | MediLockerLambda | upload, presign-upload, confirm-upload |
| `/chat` | ChatLambda | POST chat message |

---

## 11. Documentation References

| Document | Location |
|----------|----------|
| Lambda functions | `docs/LAMBDA_FUNCTIONS.md` |
| Database tables | `docs/DATABASE_TABLES.md` |
| Auth flow | `docs/AUTH_FLOW_DETAILED.md` |
| Doctor–user flow | `docs/DR_USER_CONNECTION_FLOW.md` |
| Medilocker flow | `backend/medilocker_lambda/docs/MEDILOCKER_FLOW.md` |
| Pre-auth service architecture | `docs/PREAUTH_SERVICE_ARCHITECTURE.md` |
| Hospital raw ingestion | `backend/medilocker_lambda/docs/HOSPITAL_RAW_INGESTION.md` |
| Chat flow | `backend/chat_lambda/CHAT_LAMBDA_FLOW.md` |
| Backend README | `backend/readme.md` |
