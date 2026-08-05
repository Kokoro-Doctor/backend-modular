# User-doctor relationship: where data lives

This document describes how the platform represents a connection between a patient (user) and a doctor, and which DynamoDB tables receive writes when that relationship is created or updated.

---

## Two-layer model

| Layer | Table | Purpose |
|-------|-------|---------|
| **Relationship (connectivity)** | `UserDoctor` | Persistent link between patient and doctor. Survives subscription expiry. |
| **Subscription (entitlement)** | `UserDoctorSubscriptions` | Time-limited billing record. Gates bookings and visit counters. |

The relationship layer is lightweight and carries no appointment logic. Subscriptions remain the source of truth for billing and booking limits.

---

## UserDoctor

| Attribute | Type | Key | Description |
|-----------|------|-----|-------------|
| `user_id` | S | PK | Patient ID |
| `doctor_id` | S | SK / GSI | Doctor ID |
| `relation_type` | S | -- | `USER_SUBSCRIPTION` / `HOSPITAL_ASSIGNED` / `MANUAL` |
| `status` | S | -- | `ACTIVE` / `INACTIVE` |
| `linked_by` | S | -- | `system` / `hospital_staff` / `doctor` |
| `hospital_id` | S | -- | Optional. Set for hospital-assigned relations. |
| `subscription_id` | S | -- | Optional. Links to the subscription that triggered the relation. |
| `created_at` | S | -- | ISO 8601 |
| `updated_at` | S | -- | ISO 8601 |

**GSIs:**

- Base table -- PK: `user_id`, SK: `doctor_id` (all doctors for a patient)
- `GSI_DoctorUsers` -- PK: `doctor_id`, SK: `user_id` (all patients for a doctor)

**Idempotency:** One row per `(user_id, doctor_id)` pair. Re-posting the same pair updates the existing row and sets it ACTIVE.

---

## UserDoctorSubscriptions (UNCHANGED)

| Attribute | Type | Key | Description |
|-----------|------|-----|-------------|
| `subscription_id` | S | PK | UUID |
| `user_id` | S | GSI | Patient ID |
| `doctor_id` | S | GSI | Doctor ID |
| `plan_id` | S | -- | Plan reference |
| `status` | S | -- | `ACTIVE` / `EXPIRED` / `EXHAUSTED` / `CANCELLED` |
| `start_date` / `end_date` | S | -- | Validity window |
| `appointments_total` / `appointments_used` | N | -- | Usage counters |
| `payment_id` | S | GSI | Razorpay or synthetic payment reference |

---

## When writes happen

```text
PAID FLOW
  User pays for plan
    -> PaymentsTable          (payment record)
    -> UserDoctorSubscriptions (subscription row)
    -> UserDoctor              (ACTIVE relation, type=USER_SUBSCRIPTION, linked_by=system)
    -> Earnings ledger         (if applicable)

HOSPITAL STAFF (via hospitals_lambda)
  Single add: client sends doctor_id (attending doctor).
    -> Hospital comes from JWT; doctor must be affiliated through DoctorHospital.
    -> Users table             (new user if needed; no patient.hospital_id)
    -> UserHospital            (ACTIVE membership for the JWT hospital)
    -> UserDoctor              (ACTIVE relation, type=HOSPITAL_ASSIGNED, linked_by=hospital_staff, hospital_id from JWT)
       No subscription row.

TEST / ADMIN SUBSCRIPTION
  Admin creates subscription
    -> UserDoctorSubscriptions (test subscription row)
    -> UserDoctor              (ACTIVE relation, type=USER_SUBSCRIPTION, linked_by=system)
```

---

## API surfaces

### Relation endpoints (via booking_lambda service functions)

| Function | Description |
|----------|-------------|
| `create_relation(user_id, doctor_id, ...)` | Idempotent upsert of ACTIVE relation |
| `sync_relation_for_subscription(user_id, doctor_id, subscription_id)` | Called internally after every subscription creation |
| `get_user_doctors(user_id)` | All ACTIVE doctors for a user |
| `get_doctor_patients(doctor_id)` | All ACTIVE patients for a doctor |
| `deactivate_relation(user_id, doctor_id)` | Set status = INACTIVE |

### Subscription endpoints (unchanged)

- `GET /booking/users/{user_id}/subscriptions` -- All subscription rows for a user
- `GET /booking/doctors/{doctor_id}/subscribers` -- Subscription records for doctor
- `GET /booking/doctors/{doctor_id}/patients` -- All active UserDoctor relations for doctor
- `GET /booking/subscriptions/validate` -- Booking entitlement check

---

## Backfill

Existing legacy relationship/subscription data can be backfilled into `UserDoctor` using:

```bash
cd backend/scripts/backfill_relations
AWS_REGION=ap-south-1 python backfill_relations.py --dry-run   # preview
AWS_REGION=ap-south-1 python backfill_relations.py             # execute
```

The script scans legacy `UserDoctorRelations` and `UserDoctorSubscriptions`, then upserts one row per `(user_id, doctor_id)` into `UserDoctor`.

---

## Key principles

- **Relation != Subscription.** A patient can be linked to a doctor without any active plan (e.g. hospital-assigned).
- **Subscription expiry does NOT break the link.** The relation stays ACTIVE; only the subscription status changes.
- **No changes to Users or Doctors tables.** Neither table stores a `primary_doctor_id` or similar foreign key.
- **Subscriptions remain the source of truth for entitlements.** Booking validation still checks `UserDoctorSubscriptions`.

For full table schemas, see [DATABASE_TABLES.md](./DATABASE_TABLES.md). For the narrative user flow, see [DR_USER_CONNECTION_FLOW.md](./DR_USER_CONNECTION_FLOW.md).
