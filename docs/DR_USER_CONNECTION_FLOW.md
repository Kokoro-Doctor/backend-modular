# Doctor–User Connection Flow

This document describes the end-to-end flow of how doctors and users connect in the Kokoro platform—from registration to subscription to viewing subscribers.

---

## Overview

```
Doctor Registers → User Registers → User Subscribes to Doctor → Doctor Sees Subscribers
```

---

## 1. Doctor Registration (First)

The doctor must be registered before users can subscribe to them.

**Flow:**
- Doctor enters: phone, email, OTP, name, specialization, experience
- Backend validates OTP, creates doctor profile, creates auth record
- Doctor completes medical registration (optional step)
- Doctor gets `doctor_id`

**Key endpoints:**
- `POST /auth/doctor/request-signup-otp` — Request OTP
- `POST /auth/doctor/signup` — Complete doctor signup

**Frontend:** `DoctorsSignUp.jsx`, `DoctorMedicalRegistration.jsx`

---

## 2. User Registration

The user must be registered before they can subscribe to a doctor.

**Flow:**
- User enters: phone, email, OTP, name (or mobile-only in experimental flow)
- Backend validates OTP, creates user profile, creates auth record
- User gets `user_id`

**Key endpoints:**
- `POST /auth/user/request-signup-otp` — Request OTP
- `POST /auth/user/signup` — Complete user signup

**Frontend:** `PatientAuthModal.jsx`, user signup screens

---

## 3. Doctor Creates Subscription Plans (Optional but Recommended)

Doctors define plans that users can buy. Plans are stored in the booking service.

**Flow:**
- Doctor creates plans with: price, validity days, appointments allowed, doctor_id (or "ALL")
- Plans are linked to the doctor via `doctor_id`

**Key endpoints:**
- `POST /booking/plans` — Create subscription plan
- `GET /booking/plans?doctor_id=...` — List plans for a doctor

---

## 4. User Subscribes to Doctor

The user browses doctors, selects one, chooses a plan, and pays. After successful payment, a subscription is created linking the user to the doctor.

**Flow:**
1. User browses doctors (e.g. `DoctorsInfoWithSubscription.jsx`)
2. User selects a doctor and a plan (weekly/monthly)
3. User clicks "Continue to Payment"
4. Frontend calls `POST /process-payment/payment-link` with `plan_id`, `user_id`, `doctor_id`
5. User is redirected to Razorpay payment link
6. User completes payment on Razorpay
7. Razorpay webhook (`payment.captured`) triggers `verify_payment`
8. `verify_payment` creates subscription via `create_user_subscription(user_id, doctor_id, plan_id, payment_id)`
9. Subscription is stored in `UserDoctorSubscriptions`
10. The persistent doctor-patient bond is upserted in `UserDoctor`

**Key endpoints:**
- `POST /process-payment/payment-link` — Create Razorpay payment link
- `POST /process-payment/verify` — Manual payment verification (optional)
- `POST /booking/subscriptions` — Create subscription (called internally after payment)
- `GET /booking/users/{user_id}/subscriptions` — User's subscriptions

**Frontend:** `DoctorsInfoWithSubscription.jsx`, `DoctorsSubscriptionPaymentScreen.jsx`

---

## 5. Doctor Views Subscribers ("Your Subscribers")

All users who have an active subscription to a doctor are listed under that doctor's subscribers.

**Flow:**
1. Doctor navigates to "Your Subscribers" screen
2. Frontend calls `GET /booking/doctors/{doctor_id}/subscribers`
3. Backend queries `user_doctor_subscriptions` via GSI `GSI_DoctorSubscribers` on `doctor_id`
4. For each subscription, user details are fetched from `GET /users/{user_id}`
5. List is displayed with user name, age, gender, condition, status, date

**Key endpoints:**
- `GET /booking/doctors/{doctor_id}/subscribers` — List all subscribers for a doctor

**Frontend:** `DoctorsSubscribers.jsx`

---

## Data Model Summary

| Table / Concept | Purpose |
|-----------------|---------|
| `doctors` | Doctor profiles (doctor_id, name, specialization, etc.) |
| `users` | User profiles (user_id, name, etc.) |
| `subscription_plans` | Plans (plan_id, doctor_id, price, validity_days, appointments_allowed) |
| `user_doctor_subscriptions` | Links user ↔ doctor after payment (user_id, doctor_id, plan_id, status, start_date, end_date) |
| `UserDoctor` | Unified doctor-patient relationship table for subscriptions and hospital assignments |

---

## Sequence Diagram (Simplified)

```
Doctor                    System                     User
  |                         |                          |
  |-- Register ------------->|                          |
  |<-- doctor_id ------------|                          |
  |                         |<-------- Register -------|
  |                         |-------- user_id -------->|
  |                         |                          |
  |                         |<-- Browse doctors -------|
  |                         |<-- Select plan + Pay ----|
  |                         |-- Create subscription -->|
  |                         |                          |
  |-- View "Your Subscribers"->|                        |
  |<-- List of users -------|                          |
```

---

## Related Docs

- [AUTH_FLOW_DETAILED.md](./AUTH_FLOW_DETAILED.md) — Auth and OTP flows
- [LAMBDA_FUNCTIONS.md](./LAMBDA_FUNCTIONS.md) — API endpoints
- [PAYMENT_SYSTEM_ANALYSIS.md](./PAYMENT_SYSTEM_ANALYSIS.md) — Payment flow details
