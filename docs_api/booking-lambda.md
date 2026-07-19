# Booking Lambda

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

**With optional plan_id:**

```json
{
  "plan_id": "PLAN_999_30D_DOC123",
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "price": 999.0,
  "appointments_allowed": 10,
  "validity_days": 30,
  "valid_from": "2025-01-15T00:00:00Z"
}
```

**Minimal (valid_to optional, is_active defaults to true, plan_id auto-generated):**

```json
{
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "price": 999.0,
  "appointments_allowed": 10,
  "validity_days": 30,
  "valid_from": "2025-01-15T00:00:00Z"
}
```

**Note:**

- `plan_id` is optional. If not provided, auto-generates using format: `PLAN_<PRICE>_<DURATION>D_<SCOPE>`
- `plan_id` is ONLY an identifier. The database is ALWAYS the source of truth for plan attributes (price, validity, etc.).
- `valid_to` can be `null` for plans without expiration.
- `is_active` defaults to `true` if not provided.

---

#### GET `/booking/plans/{plan_id}`

Get a subscription plan by ID.

**Path Parameters:**

- `plan_id`: Plan ID (human-readable format: `PLAN_<PRICE>_<DURATION>D_<SCOPE>`, e.g., `PLAN_999_30D_ALL`)

**Example:**

```
GET /booking/plans/PLAN_999_30D_ALL
```

**Note:** Always fetches plan data from database. Never derives plan attributes from plan_id format. The database is the source of truth.

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

Create a user subscription after successful payment. This endpoint should be called by the payment service after payment verification. It also upserts the persistent `(user_id, doctor_id)` bond into `UserDoctor` with `relation_type="USER_SUBSCRIPTION"`.

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

#### GET `/booking/doctors/{doctor_id}/patients`

Get all active patients linked to a doctor from the unified `UserDoctor` table. Includes both hospital-assigned patients and users who subscribed from the patient portal.

**Path Parameters:**

- `doctor_id`: Doctor ID

**Example:**

```
GET /booking/doctors/DOC_12345678-1234-1234-1234-123456789012/patients
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

#### POST `/booking/admin/test-subscription`

Create a test subscription directly without payment verification. This endpoint is for testing purposes only.

**Headers:**

```
x-admin-key: YOUR_ADMIN_KEY_HERE
```

**Body:**

```json
{
  "user_id": "USR_12345678-1234-1234-1234-123456789012",
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "plan_id": "PLAN_999_30D_ALL"
}
```

**Response:**

```json
{
  "subscription_id": "SUB_12345678-1234-1234-1234-123456789012",
  "user_id": "USR_12345678-1234-1234-1234-123456789012",
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "plan_id": "PLAN_999_30D_ALL",
  "plan_price": 999.0,
  "appointments_total": 10,
  "appointments_used": 0,
  "status": "ACTIVE",
  "start_date": "2025-01-31T12:00:00Z",
  "end_date": "2025-03-02T12:00:00Z",
  "payment_id": "TEST_1738320000_a1b2c3d4",
  "created_at": "2025-01-31T12:00:00Z"
}
```

**Note:**

- Requires `x-admin-key` header for authorization. Set `ADMIN_KEY` environment variable in Lambda configuration.
- Creates a subscription with a test payment*id format: `TEST*<timestamp>\_<uuid>`
- Validates that the plan exists and is active before creating subscription.
- Plan must be valid for the specified doctor (either doctor-specific or global plan with `doctor_id='ALL'`).

---
