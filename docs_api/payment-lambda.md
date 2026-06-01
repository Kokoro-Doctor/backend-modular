# Payment Lambda

### POST `/process-payment/payment-link`

Create a Razorpay payment link. Amount is derived from the plan_id - plan price is fetched from database.

```json
{
  "plan_id": "PLAN_999_30D_ALL",
  "user_id": "USR_12345678-1234-1234-1234-123456789012",
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012"
}
```

**Minimal (plan_id only):**

```json
{
  "plan_id": "PLAN_999_30D_ALL"
}
```

**Response:**

```json
{
  "message": "Payment link created successfully",
  "payment_link": "https://rzp.io/i/abc123",
  "plan_id": "PLAN_999_30D_ALL",
  "amount": 999.0
}
```

**Note:**

- `plan_id` is required. Amount is fetched from the plan in the database, not from the request.
- `user_id` and `doctor_id` are optional but recommended for subscription mapping.
- Plan ID format is human-readable (e.g., `PLAN_999_30D_ALL`) but database is the source of truth for plan attributes.

---

### POST `/process-payment/webhook`

Razorpay webhook endpoint. Razorpay sends payment events here automatically when payment is captured.

**Note:** This endpoint is called automatically by Razorpay. It verifies the webhook signature and updates the database. Payment verification and subscription creation happen automatically via webhook.

**Request:** Razorpay webhook payload (automatically sent by Razorpay)

**Response:**

```json
{
  "status": "success"
}
```

---
