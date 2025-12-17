# API Test Bodies

This document contains test request bodies for all endpoints across all Lambda functions. Update this file when endpoints or request schemas change.

---

## Table of Contents

- [Auth Lambda](#auth-lambda)
- [Chat Lambda](#chat-lambda)
- [Doctors Service Lambda](#doctors-service-lambda)
- [Booking Lambda](#booking-lambda)
- [Payment Lambda](#payment-lambda)
- [Medilocker Lambda](#medilocker-lambda)

---

## Auth Lambda

### POST `/auth/user/request-signup-otp`

Request OTP for user signup.

```json
{
  "phoneNumber": "+919587733170"
}
```

**Alternative formats:**

```json
{
  "phoneNumber": "9587733170"
}
```

```json
{
  "phoneNumber": "919587733170"
}
```

---

### POST `/auth/doctor/request-signup-otp`

Request OTP for doctor signup.

```json
{
  "phoneNumber": "+919587733170"
}
```

---

### POST `/auth/request-otp`

Request OTP for login (existing users/doctors).

```json
{
  "phoneNumber": "+919587733170"
}
```

---

### POST `/auth/login`

Login with OTP (or get role discovery if OTP not provided).

**With OTP:**

```json
{
  "phoneNumber": "+919587733170",
  "otp": "1234"
}
```

**Without OTP (role discovery):**

```json
{
  "phoneNumber": "+919587733170"
}
```

---

### POST `/auth/user/signup`

Complete user signup after OTP verification.

```json
{
  "phoneNumber": "+919587733170",
  "otp": "1234",
  "name": "John Doe",
  "email": "john.doe@example.com"
}
```

**Minimal (without email):**

```json
{
  "phoneNumber": "+919587733170",
  "otp": "1234",
  "name": "John Doe"
}
```

---

### POST `/auth/doctor/signup`

Complete doctor signup after OTP verification.

```json
{
  "phoneNumber": "+919587733170",
  "otp": "1234",
  "name": "Dr. Jane Smith",
  "specialization": "Cardiologist",
  "experience": 10,
  "email": "jane.smith@example.com"
}
```

**Minimal:**

```json
{
  "phoneNumber": "+919587733170",
  "otp": "1234",
  "name": "Dr. Jane Smith"
}
```

---

### POST `/auth/google`

Google OAuth login/signup.

```json
{
  "token": "eyJhbGciOiJSUzI1NiIsImtpZCI6Ij..."
}
```

---

### POST `/auth/admin/delete-account`

Delete user or doctor account (requires `x-admin-key` header).

**Headers:**

```
x-admin-key: YOUR_ADMIN_KEY_HERE
```

**Body:**

```json
{
  "phoneNumber": "+919587733170"
}
```

---

## Chat Lambda

### POST `/chat`

Send a chat message and get AI response.

**With user_id:**

```json
{
  "user_id": "USR_12345678-1234-1234-1234-123456789012",
  "message": "What are the symptoms of high blood pressure?",
  "language": "en"
}
```

**With session_id:**

```json
{
  "session_id": "session_abc123",
  "message": "How can I improve my heart health?",
  "language": "hi"
}
```

**With doctor_id:**

```json
{
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "message": "Tell me about cholesterol management",
  "language": "en"
}
```

**Language options:** `en`, `hi`, `es`, `te`

---

## Doctors Service Lambda

### POST `/doctorsService/setSlots`

Set availability slots for a doctor on a specific date.

```json
{
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "date": "2025-01-15",
  "slots": [
    {
      "start": "09:00",
      "end": "09:30"
    },
    {
      "start": "10:00",
      "end": "10:30"
    },
    {
      "start": "11:00",
      "end": "11:30"
    }
  ]
}
```

---

### POST `/doctorsService/updateSlot`

Update a specific slot's availability.

```json
{
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "date": "2025-01-15",
  "slot_time": "10:00",
  "available": false
}
```

**Enable slot:**

```json
{
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "date": "2025-01-15",
  "slot_time": "10:00",
  "available": true
}
```

---

### POST `/doctorsService/updateProfile`

Update doctor profile with optional documents.

**Without documents:**

```json
{
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "description": "Experienced cardiologist with 15 years of practice",
  "specialization": "Cardiology",
  "experience": "15",
  "fees": 500,
  "timings": "9:00 AM - 6:00 PM",
  "licenseNumber": "MED123456",
  "registrationId": "REG789012",
  "affiliation": "ABC Hospital"
}
```

**With documents (base64 encoded):**

```json
{
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "description": "Experienced cardiologist",
  "specialization": "Cardiology",
  "degreeCertificate": {
    "filename": "degree.pdf",
    "base64_content": "JVBERi0xLjQKJeLjz9MKMy..."
  },
  "govtIdProof": {
    "filename": "aadhaar.jpg",
    "base64_content": "/9j/4AAQSkZJRgABAQAAAQ..."
  },
  "profilePhoto": {
    "filename": "photo.jpg",
    "base64_content": "/9j/4AAQSkZJRgABAQAAAQ..."
  }
}
```

---

### GET `/doctorsService/doctors`

Fetch doctors list, optionally filtered by category.

**Query Parameters (optional):**

- `category`: Filter by category (e.g., "Cardiologist")

**Examples:**

```
GET /doctorsService/doctors
```

```
GET /doctorsService/doctors?category=Cardiologist
```

---

> **Note:** The `/doctorsService/subscribe` endpoint has been removed. Subscriptions are now managed via the Booking Lambda (`/booking/subscriptions`) and are created automatically after successful payment. See Subscription System documentation.

---

## Booking Lambda

The Booking Lambda contains two routers:

```
BookingLambda
 ├── AppointmentRouter (appointment booking and management)
 └── SubscriptionRouter (subscription plans and user subscriptions)
```

---

### AppointmentRouter

#### POST `/booking/bookings`

Book an appointment slot atomically. Checks availability and creates booking if available. Validates subscription before booking.

```json
{
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "date": "2025-01-15",
  "start_time": "10:00",
  "user_id": "USR_12345678-1234-1234-1234-123456789012"
}
```

---

#### DELETE `/booking/bookings/{booking_id}`

Cancel a booking by booking ID. Marks slot as available and removes booking.

**Path Parameter:**

- `booking_id`: The booking ID to cancel

**Example:**

```
DELETE /booking/bookings/550e8400-e29b-41d4-a716-446655440000
```

---

#### GET `/booking/doctors/{doctor_id}/bookings`

Get all bookings for a doctor. Returns sorted by time. If date provided, returns bookings for that date only.

**Path Parameters:**

- `doctor_id`: Doctor identifier

**Query Parameters (optional):**

- `date`: Filter by date (YYYY-MM-DD format)

**Examples:**

```
GET /booking/doctors/DOC_12345678-1234-1234-1234-123456789012/bookings
```

```
GET /booking/doctors/DOC_12345678-1234-1234-1234-123456789012/bookings?date=2025-01-15
```

---

#### GET `/booking/users/{user_id}/bookings`

Get all bookings for a user. Uses GSI on user_id. Returns sorted by date and time.

**Path Parameters:**

- `user_id`: User identifier

**Query Parameters (optional):**

- `type`: Filter by type (`upcoming` or `past`)
  - `upcoming`: Returns only future bookings
  - `past`: Returns only past bookings

**Examples:**

```
GET /booking/users/USR_12345678-1234-1234-1234-123456789012/bookings
```

```
GET /booking/users/USR_12345678-1234-1234-1234-123456789012/bookings?type=upcoming
```

```
GET /booking/users/USR_12345678-1234-1234-1234-123456789012/bookings?type=past
```

---

#### GET `/booking/doctors/{doctor_id}/availability`

Get slot availability for a doctor on a specific date. Returns slot_time, available status, and booking_id if booked.

**Path Parameters:**

- `doctor_id`: Doctor identifier

**Query Parameters:**

- `date`: Date in YYYY-MM-DD format (required)

**Example:**

```
GET /booking/doctors/DOC_12345678-1234-1234-1234-123456789012/availability?date=2025-01-15
```

---

#### GET `/booking/doctors/{doctor_id}/calendar`

Get unified calendar for a doctor showing availability + bookings for next N days. Merges data from both AvailabilityTable and BookingsTable.

**Path Parameters:**

- `doctor_id`: Doctor identifier

**Query Parameters (optional):**

- `days`: Number of days to include (default: 7, min: 1, max: 30)

**Examples:**

```
GET /booking/doctors/DOC_12345678-1234-1234-1234-123456789012/calendar
```

```
GET /booking/doctors/DOC_12345678-1234-1234-1234-123456789012/calendar?days=14
```

---

### SubscriptionRouter

#### POST `/booking/plans`

Create a new subscription plan. Plans can be global (doctor_id='ALL') or doctor-specific.

```json
{
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "price": 999.0,
  "appointments_allowed": 10,
  "validity_days": 30,
  "valid_from": "2025-01-15T00:00:00Z",
  "valid_to": "2025-12-31T23:59:59Z",
  "is_active": true
}
```

**Global plan (for all doctors):**

```json
{
  "doctor_id": "ALL",
  "price": 1499.0,
  "appointments_allowed": 20,
  "validity_days": 60,
  "valid_from": "2025-01-15T00:00:00Z",
  "valid_to": null,
  "is_active": true
}
```

**Minimal (valid_to optional, is_active defaults to true):**

```json
{
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "price": 999.0,
  "appointments_allowed": 10,
  "validity_days": 30,
  "valid_from": "2025-01-15T00:00:00Z"
}
```

---

#### GET `/booking/plans/{plan_id}`

Get a subscription plan by ID.

**Path Parameters:**

- `plan_id`: Plan ID

**Example:**

```
GET /booking/plans/PLAN_12345678-1234-1234-1234-123456789012
```

---

#### GET `/booking/plans`

List subscription plans. If doctor_id provided, returns active plans for that doctor (including global plans). Otherwise returns all active plans.

**Query Parameters (optional):**

- `doctor_id`: Filter by doctor ID or 'ALL' for global plans

**Examples:**

```
GET /booking/plans
```

```
GET /booking/plans?doctor_id=DOC_12345678-1234-1234-1234-123456789012
```

```
GET /booking/plans?doctor_id=ALL
```

---

#### PUT `/booking/plans/{plan_id}`

Update a subscription plan. Note: Consider creating new plan versions for immutability.

**Path Parameters:**

- `plan_id`: Plan ID

**Body (all fields optional):**

```json
{
  "price": 1299.0,
  "appointments_allowed": 15,
  "validity_days": 45,
  "valid_from": "2025-02-01T00:00:00Z",
  "valid_to": "2025-12-31T23:59:59Z",
  "is_active": false
}
```

**Example:**

```
PUT /booking/plans/PLAN_12345678-1234-1234-1234-123456789012
```

---

#### POST `/booking/subscriptions`

Create a user subscription after successful payment. This endpoint should be called by the payment service after payment verification.

```json
{
  "user_id": "USR_12345678-1234-1234-1234-123456789012",
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "plan_id": "PLAN_12345678-1234-1234-1234-123456789012",
  "payment_id": "pay_1234567890abcdef"
}
```

---

#### GET `/booking/subscriptions/{subscription_id}`

Get a subscription by ID.

**Path Parameters:**

- `subscription_id`: Subscription ID

**Example:**

```
GET /booking/subscriptions/SUB_12345678-1234-1234-1234-123456789012
```

---

#### GET `/booking/users/{user_id}/subscriptions`

Get all subscriptions for a user.

**Path Parameters:**

- `user_id`: User ID

**Example:**

```
GET /booking/users/USR_12345678-1234-1234-1234-123456789012/subscriptions
```

---

#### GET `/booking/doctors/{doctor_id}/subscribers`

Get all subscribers for a doctor.

**Path Parameters:**

- `doctor_id`: Doctor ID

**Example:**

```
GET /booking/doctors/DOC_12345678-1234-1234-1234-123456789012/subscribers
```

---

#### GET `/booking/subscriptions/validate`

Validate if user has an active subscription for booking. Used by booking service before allowing appointment booking.

**Query Parameters:**

- `user_id`: User ID (required)
- `doctor_id`: Doctor ID (required)

**Example:**

```
GET /booking/subscriptions/validate?user_id=USR_12345678-1234-1234-1234-123456789012&doctor_id=DOC_12345678-1234-1234-1234-123456789012
```

**Response:**

```json
{
  "is_valid": true,
  "subscription_id": "SUB_12345678-1234-1234-1234-123456789012",
  "appointments_remaining": 8,
  "status": "ACTIVE",
  "message": "Subscription is valid"
}
```

---

#### POST `/booking/subscriptions/increment`

Atomically increment appointments_used for a subscription. Called by booking service after successful appointment booking.

```json
{
  "subscription_id": "SUB_12345678-1234-1234-1234-123456789012",
  "user_id": "USR_12345678-1234-1234-1234-123456789012",
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012"
}
```

---

#### POST `/booking/subscriptions/{subscription_id}/cancel`

Cancel a subscription.

**Path Parameters:**

- `subscription_id`: Subscription ID

**Query Parameters:**

- `user_id`: User ID (required)

**Example:**

```
POST /booking/subscriptions/SUB_12345678-1234-1234-1234-123456789012/cancel?user_id=USR_12345678-1234-1234-1234-123456789012
```

---

## Payment Lambda

### POST `/process-payment`

Unified payment endpoint for backward compatibility. Routes to create payment link or verify payment based on request body.

**Create Payment Link (with amount):**

```json
{
  "amount": 999.0,
  "plan_id": "PLAN_12345678-1234-1234-1234-123456789012",
  "user_id": "USR_12345678-1234-1234-1234-123456789012",
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012"
}
```

**Minimal (amount only):**

```json
{
  "amount": 500.0
}
```

**Verify Payment (with payment_id):**

```json
{
  "payment_id": "pay_1234567890abcdef",
  "plan_id": "PLAN_12345678-1234-1234-1234-123456789012",
  "user_id": "USR_12345678-1234-1234-1234-123456789012",
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012"
}
```

**Minimal (payment_id only):**

```json
{
  "payment_id": "pay_1234567890abcdef"
}
```

---

### POST `/process-payment/payment-link`

Create a Razorpay payment link.

```json
{
  "amount": 999.0,
  "plan_id": "PLAN_12345678-1234-1234-1234-123456789012",
  "user_id": "USR_12345678-1234-1234-1234-123456789012",
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012"
}
```

**Minimal (amount only):**

```json
{
  "amount": 500.0
}
```

**Response:**

```json
{
  "message": "Payment link created successfully",
  "payment_link": "https://rzp.io/i/abc123"
}
```

---

### POST `/process-payment/verify-payment`

Verify payment with Razorpay and store in DynamoDB. If subscription metadata (plan_id, user_id, doctor_id) is provided, creates subscription after successful payment.

```json
{
  "payment_id": "pay_1234567890abcdef",
  "plan_id": "PLAN_12345678-1234-1234-1234-123456789012",
  "user_id": "USR_12345678-1234-1234-1234-123456789012",
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012"
}
```

**Minimal (payment_id only):**

```json
{
  "payment_id": "pay_1234567890abcdef"
}
```

**Response (successful payment):**

```json
{
  "message": "Payment processed",
  "order_id": "order_1234567890abcdef",
  "payment_id": "pay_1234567890abcdef",
  "status": "captured",
  "invoice_url": "https://yourwebsite.com/invoices/pay_1234567890abcdef",
  "subscription_id": "SUB_12345678-1234-1234-1234-123456789012"
}
```

**Response (failed payment):**

```json
{
  "message": "Payment processed",
  "order_id": "order_1234567890abcdef",
  "payment_id": "pay_1234567890abcdef",
  "status": "failed",
  "invoice_url": null,
  "subscription_id": null
}
```

---

## Medilocker Lambda

### POST `/medilocker/upload`

Upload medical files to user's medilocker.

```json
{
  "user_id": "USR_12345678-1234-1234-1234-123456789012",
  "files": [
    {
      "filename": "prescription.pdf",
      "content": "JVBERi0xLjQKJeLjz9MKMy...",
      "metadata": {
        "type": "prescription",
        "date": "2025-01-15"
      }
    },
    {
      "filename": "lab_report.jpg",
      "content": "/9j/4AAQSkZJRgABAQAAAQ...",
      "metadata": {
        "type": "lab_report",
        "date": "2025-01-10"
      }
    }
  ]
}
```

**Single file:**

```json
{
  "user_id": "USR_12345678-1234-1234-1234-123456789012",
  "files": [
    {
      "filename": "prescription.pdf",
      "content": "JVBERi0xLjQKJeLjz9MKMy...",
      "metadata": {}
    }
  ]
}
```

---

### GET `/medilocker/users/{user_id}/files`

Fetch list of files for a user.

**Path Parameters:**

- `user_id`: User ID (required)

**Example:**

```
GET /medilocker/users/USR_12345678-1234-1234-1234-123456789012/files
```

---

### GET `/medilocker/users/{user_id}/files/{filename}/download`

Generate presigned download URL for a file.

**Path Parameters:**

- `user_id`: User ID (required)
- `filename`: Filename (required, supports path segments, e.g., `folder/file.pdf`)

**Example:**

```
GET /medilocker/users/USR_12345678-1234-1234-1234-123456789012/files/prescription.pdf/download
```

---

### DELETE `/medilocker/users/{user_id}/files/{filename}`

Delete a file from user's medilocker.

**Path Parameters:**

- `user_id`: User ID
- `filename`: Filename (supports path segments, e.g., `folder/file.pdf`)

**Example:**

```
DELETE /medilocker/users/USR_12345678-1234-1234-1234-123456789012/files/prescription.pdf
```

---

### POST `/medilocker/extract-structured-data`

Extract structured prescription data from uploaded files using GPT-4 Vision.

**With files only:**

```json
{
  "files": [
    {
      "filename": "prescription.pdf",
      "content": "JVBERi0xLjQKJeLjz9MKMy...",
      "metadata": {}
    },
    {
      "filename": "lab_report.jpg",
      "content": "/9j/4AAQSkZJRgABAQAAAQ...",
      "metadata": {}
    }
  ]
}
```

**With frontend patient details:**

```json
{
  "files": [
    {
      "filename": "prescription.pdf",
      "content": "JVBERi0xLjQKJeLjz9MKMy...",
      "metadata": {}
    }
  ],
  "frontend_patient_details": {
    "name": "John Doe",
    "age": "45",
    "dob": "1980-01-15",
    "sex": "Male",
    "weight": "75",
    "allergies": "Penicillin",
    "pregnancy_bf": ""
  }
}
```

**Note:** Files should contain base64-encoded content. Supported formats: PDF, images (JPG, PNG, GIF, WEBP), and text files.

---

## Notes

### Phone Number Formats

All phone numbers are normalized to E.164 format. Accepted formats:

- `+919587733170` (with country code)
- `9587733170` (10 digits, assumes +91)
- `919587733170` (12 digits, adds +)

### Date Formats

- Dates: `YYYY-MM-DD` (e.g., `2025-01-15`)
- Times: `HH:MM` (24-hour format, e.g., `10:00`, `14:30`)

### Base64 Encoding

For file uploads, ensure content is base64-encoded. Example tools:

- Online: https://www.base64encode.org/
- Command line: `base64 -i file.pdf`

### Authentication

Most endpoints require authentication via JWT token in `Authorization` header:

```
Authorization: Bearer <jwt_token>
```

Admin endpoints require `x-admin-key` header instead.

---

## Update Log

- **2025-01-XX**: Initial documentation created
- **2025-01-XX**: Updated Booking Lambda endpoints to include `/booking` prefix:
  - `POST /bookings` → `POST /booking/bookings`
  - `DELETE /bookings/{booking_id}` → `DELETE /booking/bookings/{booking_id}`
  - `GET /doctors/{doctor_id}/bookings` → `GET /booking/doctors/{doctor_id}/bookings`
  - `GET /users/{user_id}/bookings` → `GET /booking/users/{user_id}/bookings`
  - `GET /doctors/{doctor_id}/availability` → `GET /booking/doctors/{doctor_id}/availability`
  - `GET /doctors/{doctor_id}/calendar` → `GET /booking/doctors/{doctor_id}/calendar`
  - Renamed section from "Appointment Service Lambda" to "Booking Lambda"
  - Added detailed endpoint descriptions matching router implementation
- **2025-01-XX**: Added Payment Lambda endpoints:
  - `POST /process-payment` - Unified endpoint for creating payment links or verifying payments
  - `POST /process-payment/payment-link` - Create Razorpay payment link
  - `POST /process-payment/verify-payment` - Verify payment and optionally create subscription
- **2025-01-XX**: Reorganized Booking Lambda section to include both Booking and Subscription routers:
  - Added "Booking Endpoints" subsection with all appointment booking endpoints
  - Added "Subscription Endpoints" subsection with subscription plan and user subscription endpoints:
    - `POST /booking/plans` - Create subscription plan
    - `GET /booking/plans/{plan_id}` - Get plan by ID
    - `GET /booking/plans` - List plans (with optional doctor_id filter)
    - `PUT /booking/plans/{plan_id}` - Update plan
    - `POST /booking/subscriptions` - Create user subscription
    - `GET /booking/subscriptions/{subscription_id}` - Get subscription by ID
    - `GET /booking/users/{user_id}/subscriptions` - Get user subscriptions
    - `GET /booking/doctors/{doctor_id}/subscribers` - Get doctor subscribers
    - `GET /booking/subscriptions/validate` - Validate subscription for booking
    - `POST /booking/subscriptions/increment` - Increment appointments used
    - `POST /booking/subscriptions/{subscription_id}/cancel` - Cancel subscription
