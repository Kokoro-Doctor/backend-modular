# Lambda Functions Overview

## Authentication & User Management

### AuthLambda (`/auth`)

User/doctor signup, login, OAuth, password reset, OTP verification, account deletion

**Endpoints:**

- `POST /auth/user/request-signup-otp` - Request signup OTP for user (sent to email)
  - Body: `{ "phoneNumber": "string", "email": "string" }`
- `POST /auth/doctor/request-signup-otp` - Request signup OTP for doctor (sent to email)
  - Body: `{ "phoneNumber": "string", "email": "string" }`
- `POST /auth/request-otp` - Request login OTP (email or SMS)
  - Body: `{ "identifier": "string", "preferredChannel": "email|sms" }`
- `POST /auth/login` - Login with OTP
  - Body: `{ "identifier": "string", "otp": "string" }`
- `POST /auth/user/signup` - Create user account with OTP verification
  - Body: `{ "phoneNumber": "string", "email": "string", "otp": "string", "name": "string" }`
- `POST /auth/doctor/signup` - Create doctor account with OTP verification
  - Body: `{ "phoneNumber": "string", "email": "string", "otp": "string", "name": "string", "specialization": "string", "experience": 0 }`
- `POST /auth/google` - Sign in via Google OAuth token
  - Body: `{ "token": "string" }`
- `POST /auth/session/initiate` - Create anonymous session for unauthenticated users
  - Body: `{}`
- `POST /auth/admin/delete-account` - Delete user/doctor/hospital account data (admin only)
  - Body: `{ "phoneNumber": "string" }`, `{ "hospital_id": "string" }`, or both

### UserServiceLambda (`/users`)

User profile retrieval and management

**Endpoints:**

- `GET /users/{user_id}` - Get user profile by ID

## Doctor Services

### HospitalsLambda (`/hospitals`)

Hospital CRUD, login, staff workflows, and user-doctor relation views.

**Endpoints:**

- `POST /hospitals/create` - Create hospital; `hospital_id` is generated server-side
  - Body: `{ "name": "string", "api_key": "string", "address": "string", "city": "string", "state": "string", "contact_number": "string", "email": "string" }`
- `POST /hospitals/login` - Validate hospital API key and return JWT
- `GET /hospitals/list` - List all active hospitals
- `GET /hospitals/get/{hospital_id}` - Get hospital by ID
- `PUT /hospitals/update/{hospital_id}` - Update hospital metadata
- `PUT /hospitals/disable/{hospital_id}` - Soft delete hospital
- `GET /hospitals/users/{user_id}/doctors` - List active doctors linked to user
- `GET /hospitals/users/{user_id}/doctors/{doctor_id}/relation` - Get user-doctor relation details
- `DELETE /hospitals/users/{user_id}/doctors/{doctor_id}` - Deactivate user-doctor relation
- `GET /hospitals/doctors/{doctor_id}/patients` - List active patients for doctor
- `GET /hospitals/doctors/{doctor_id}/patients/count` - Count active patients for doctor
- `GET /hospitals/{hospital_id}/patients` - List unique patients assigned through hospital relations
- `GET /hospitals/{hospital_id}/doctors` - List hospital doctors with patient counts
- `GET /hospitals/{hospital_id}/relations` - List doctor-patient assignments for hospital
- `POST /hospitals/relations` - Create/reactivate user-doctor relation

### DoctorsServiceLambda (`/doctorsService`)

Doctor profile updates, document uploads, doctor listings, availability slot management

**Endpoints:**

- `POST /doctorsService/updateProfile` - Update doctor profile and upload documents
  - Body: `{ "doctor_id": "string", "description": "string", "specialization": "string", "experience": "string", "fees": 0, "timings": "string", "licenseNumber": "string", "registrationId": "string", "affiliation": "string", "hospital_id": "string", "degreeCertificate": { "filename": "string", "base64_content": "string" }, "govtIdProof": { "filename": "string", "base64_content": "string" }, "profilePhoto": { "filename": "string", "base64_content": "string" } }`
- `GET /doctorsService/doctors` - Fetch doctors list (optional filters)
  - Query: `?category=string&hospital_id=HOSP_xxx`
- `POST /doctorsService/setSlots` - Bulk-create availability slots for a weekday
  - Body: `{ "doctor_id": "string", "date": "YYYY-MM-DD", "slots": [{ "start": "HH:MM", "end": "HH:MM" }] }`
- `POST /doctorsService/updateSlot` - Toggle availability for a single slot
  - Body: `{ "doctor_id": "string", "date": "YYYY-MM-DD", "slot_time": "HH:MM", "available": true }`

## Appointments & Subscriptions

### BookingLambda (`/booking`)

Appointment booking/cancellation, availability queries, subscription plans and user subscriptions management

**Appointment Endpoints:**

- `POST /booking/bookings` - Book an appointment slot atomically
  - Body: `{ "doctor_id": "string", "date": "YYYY-MM-DD", "start_time": "HH:MM", "user_id": "string" }`
- `DELETE /booking/bookings/{booking_id}` - Cancel a booking
- `GET /booking/doctors/{doctor_id}/bookings` - Get bookings for a doctor (optional date filter)
  - Query: `?date=YYYY-MM-DD` (optional)
- `GET /booking/users/{user_id}/bookings` - Get bookings for a user (optional type filter: upcoming/past)
  - Query: `?type=upcoming|past` (optional)
- `GET /booking/doctors/{doctor_id}/availability` - Get slot availability for a doctor on a date
  - Query: `?date=YYYY-MM-DD` (required)
- `GET /booking/doctors/{doctor_id}/calendar` - Get unified calendar for a doctor (availability + bookings)
  - Query: `?days=7` (optional, default 7, min 1, max 30)

**Subscription Plan Endpoints:**

- `POST /booking/plans` - Create a new subscription plan
  - Body: `{ "doctor_id": "string|ALL", "price": 0.0, "appointments_allowed": 0, "validity_days": 0, "valid_from": "ISO_DATE", "valid_to": "ISO_DATE", "is_active": true, "plan_id": "string" }`
- `GET /booking/plans/{plan_id}` - Get subscription plan by ID
- `GET /booking/plans` - List subscription plans (optional doctor_id filter)
  - Query: `?doctor_id=string` (optional)
- `PUT /booking/plans/{plan_id}` - Update subscription plan
  - Body: `{ "price": 0.0, "appointments_allowed": 0, "validity_days": 0, "valid_from": "ISO_DATE", "valid_to": "ISO_DATE", "is_active": true }`

**User Subscription Endpoints:**

- `POST /booking/subscriptions` - Create user subscription after payment
  - Body: `{ "user_id": "string", "doctor_id": "string", "plan_id": "string", "payment_id": "string" }`
- `GET /booking/subscriptions/{subscription_id}` - Get subscription by ID
- `GET /booking/users/{user_id}/subscriptions` - Get all subscriptions for a user
- `GET /booking/doctors/{doctor_id}/subscribers` - Get all subscribers for a doctor
- `GET /booking/doctors/{doctor_id}/patients` - Get all active patients linked to doctor from UserDoctor
- `GET /booking/subscriptions/validate` - Validate if user has active subscription for booking
  - Query: `?user_id=string&doctor_id=string` (both required)
- `POST /booking/subscriptions/increment` - Increment appointments_used for a subscription
  - Body: `{ "subscription_id": "string", "user_id": "string", "doctor_id": "string" }`
- `POST /booking/subscriptions/{subscription_id}/cancel` - Cancel a subscription
  - Query: `?user_id=string` (required)

## Payments

### ProcessPaymentLambda (`/process-payment`)

Razorpay payment link creation, payment verification, invoice generation, auto-creates subscriptions, creates earnings ledger entries

**Endpoints:**

- `POST /process-payment/payment-link` - Create Razorpay payment link for a subscription plan
  - Body: `{ "plan_id": "string", "user_id": "string", "doctor_id": "string" }`
- `POST /process-payment/webhook` - Razorpay webhook handler for payment events

## Doctor Earnings & Payouts

### DoctorPayoutsLambda (`/payouts`)

Doctor earnings summary, payout requests, payout history, admin payout processing

**Endpoints:**

- `POST /payouts/request` - Request a payout for a specific month
  - Body: `{ "doctor_id": "string", "payout_month": "YYYY-MM", "payout_method": "BANK|UPI" }`
- `GET /payouts/earnings/summary` - Get earnings summary for a doctor (optional month filter)
  - Query: `?doctor_id=string&month=YYYY-MM` (doctor_id required, month optional)
- `GET /payouts/{payout_id}` - Get payout details by ID
- `GET /payouts/doctors/{doctor_id}/history` - Get payout history for a doctor
- `PUT /payouts/admin/update-status` - Update payout status (admin only)
  - Body: `{ "payout_id": "string", "status": "REQUESTED|PROCESSING|COMPLETED|FAILED", "transaction_reference": "string" }`
- `GET /payouts/admin/pending` - Get all pending payouts (admin only)

## Medical Records

### MediLockerLambda (`/medilocker` and `/hospital`)

Encrypted medical file upload/download/delete, AI-generated prescription creation, clinical queries, and hospital raw data ingestion.

**Medilocker Endpoints:**

- `POST /medilocker/upload` - Upload encrypted medical files to S3
  - Body: `{ "user_id": "string", "files": [{ "filename": "string", "content": "base64_string", "metadata": {} }] }`
- `GET /medilocker/users/{user_id}/files` - List all files for a user (optional category filter)
  - Query: `?category=string` (optional)
- `GET /medilocker/users/{user_id}/files/{file_id}/download` - Generate presigned download URL (use file_id from list response)
- `DELETE /medilocker/users/{user_id}/files/{file_id}` - Delete a medical file
- `POST /medilocker/users/{user_id}/prescription` - Generate prescription from stored S3 documents (no body)
- `POST /medilocker/users/{user_id}/prescription/save` - Save approved prescription PDF to patient's Medilocker
  - Body: `{ "prescription_pdf": "base64_string" }`
- `POST /medilocker/users/{user_id}/clinical-query` - Doctor asks question about patient's stored medical records
  - Body: `{ "question": "string" }`
- `POST /medilocker/prescription` - Extract prescription from uploaded files using AI (GPT Vision)
  - Body: `{ "files": [{ "filename": "string", "content": "base64_string" }], "frontend_patient_details": {} }`

**Hospital Raw Data Endpoints** (auth: `x-hospital-api-key` header):

- `POST /hospital/upload` - Direct API upload (multipart/form-data)
  - Form: `hospital_id`, `patient_id`, `file`
- `POST /hospital/presign-upload` - Request presigned S3 URLs for batch upload
  - Body: `{ "hospital_id": "string", "patient_id": "string", "files": [{ "filename": "string" }] }`
- `POST /hospital/confirm-upload` - Confirm presigned uploads completed (saves metadata to DynamoDB)
  - Body: `{ "hospital_id": "string", "patient_id": "string", "files": [{ "file_id": "string", "filename": "string", "file_size": number }] }`

## Chat

### ChatLambda (`/chat`)

AI-powered chat assistant with RAG (Retrieval Augmented Generation) fallback to LLM

**Endpoints:**

- `POST /chat` - Send chat message and get AI response
  - Body: `{ "user_id": "string", "session_id": "string", "doctor_id": "string", "message": "string", "language": "en" }`
