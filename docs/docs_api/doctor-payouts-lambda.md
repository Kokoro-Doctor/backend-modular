# Doctor Payouts Lambda

### POST `/payouts/request`

Request a payout for a specific month. Only one payout per doctor per month is allowed. Withdrawal is only allowed after month end.

```json
{
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "payout_month": "2025-01",
  "payout_method": "BANK"
}
```

**Alternative (UPI):**

```json
{
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "payout_month": "2025-01",
  "payout_method": "UPI"
}
```

**Note:** `payout_method` must be `"BANK"` or `"UPI"`.

---

### GET `/payouts/earnings/summary`

Get earnings summary for a doctor.

**Query Parameters:**

- `doctor_id` (required)
- `month` (optional) — `YYYY-MM` format. If provided, returns summary for that month only. Otherwise returns total across all months.

**Examples:**

```
GET /payouts/earnings/summary?doctor_id=DOC_12345678-1234-1234-1234-123456789012
```

```
GET /payouts/earnings/summary?doctor_id=DOC_12345678-1234-1234-1234-123456789012&month=2025-01
```

---

### GET `/payouts/{payout_id}`

Get a payout by ID.

**Path Parameters:**

- `payout_id`: Payout ID

**Example:**

```
GET /payouts/PAY_12345678-1234-1234-1234-123456789012
```

---

### GET `/payouts/doctors/{doctor_id}/history`

Get payout history for a doctor.

**Path Parameters:**

- `doctor_id`: Doctor ID

**Example:**

```
GET /payouts/doctors/DOC_12345678-1234-1234-1234-123456789012/history
```

---

### PUT `/payouts/admin/update-status`

Update payout status (admin only). Used to mark payouts as PROCESSING, COMPLETED, or FAILED.

```json
{
  "payout_id": "PAY_12345678-1234-1234-1234-123456789012",
  "status": "COMPLETED",
  "transaction_reference": "TXN_ABC123"
}
```

**Note:** `status` must be `"REQUESTED"`, `"PROCESSING"`, `"COMPLETED"`, or `"FAILED"`. `transaction_reference` is optional.

---

### GET `/payouts/admin/pending`

Get all pending payouts (admin only). Returns payouts with status REQUESTED or PROCESSING.

**Example:**

```
GET /payouts/admin/pending
```

---
