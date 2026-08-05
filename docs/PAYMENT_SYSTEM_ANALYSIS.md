# Payment System Analysis

## 1. Payment Order Creation

### Frontend Flow
- **Location**: `frontend/utils/PaymentService.js`
- **Function**: `payment_api(planId, doctorId, userId)`
- **Endpoint**: `POST /process-payment/payment-link`
- **Request Body**:
  ```json
  {
    "plan_id": "PLAN_999_30D_ALL",
    "doctor_id": "doctor_id",
    "user_id": "user_id"
  }
  ```

### Backend Flow
- **Location**: `backend/payment_lambda/app/routers/payment_router.py`
- **Endpoint**: `POST /process-payment/payment-link`
- **Handler**: `create_payment_link_endpoint()`
- **Service**: `backend/payment_lambda/app/services/payment_service.py`
- **Function**: `create_payment_link(plan_id, user_id, doctor_id)`

### Order Creation Process
1. Fetches plan details from `SubscriptionPlans` DynamoDB table using `plan_id`
2. Validates plan exists and is active
3. Extracts `price` from plan (database is source of truth, not plan_id format)
4. Converts amount to paise (multiplies by 100)
5. Creates Razorpay payment link with:
   - `amount`: Amount in paise
   - `currency`: "INR"
   - `description`: "Payment for plan: {plan_id}"
   - `callback_url`: "https://kokoro.doctor/patient/Doctors/DoctorsInfoWithBooking"
   - `notes`: Contains `plan_id`, `user_id`, `doctor_id` (used later for webhook processing)
6. Returns `short_url` (Razorpay payment link)

**Key Code Location**: `backend/payment_lambda/app/services/payment_service.py` lines 67-130

---

## 2. Payment Success/Failure Handling

### Two Methods of Payment Verification

#### Method 1: Webhook (Primary - Automatic)
- **Endpoint**: `POST /process-payment/webhook`
- **Location**: `backend/payment_lambda/app/routers/payment_router.py` line 76
- **Handler**: `process_razorpay_webhook(request)`
- **Service**: `backend/payment_lambda/app/services/payment_service.py` lines 422-498

**Flow**:
1. Razorpay automatically sends webhook when payment is captured
2. Webhook signature is verified using HMAC SHA256 with `WEBHOOK_SECRET`
3. Listens for `payment.captured` event
4. Extracts `payment_id`, `user_id`, `plan_id`, `doctor_id` from webhook payload
5. Automatically calls `verify_payment()` internally
6. Updates DynamoDB and creates subscription if needed
7. Always returns 200 OK to Razorpay (even on errors to prevent retries)

**Signature Verification**:
```python
generated_signature = hmac.new(
    key=WEBHOOK_SECRET.encode(),
    msg=body_bytes,
    digestmod=hashlib.sha256
).hexdigest()
```

#### Method 2: Manual Verification (Not Currently Exposed)
- **Function**: `verify_payment()` exists but no public endpoint
- **Location**: `backend/payment_lambda/app/services/payment_service.py` lines 133-303
- Can be called internally by webhook handler

### Payment Status Handling

**Success (status = "captured")**:
- Payment record stored in DynamoDB `PaymentsTable`
- If `plan_id`, `user_id`, `doctor_id` provided:
  - Creates subscription via Lambda invocation to Booking Service
  - Creates earnings ledger entry for doctor
  - Generates invoice URL
- Returns success response with `payment_id`, `status`, `invoice_url`

**Failure**:
- Payment record still stored in DynamoDB with `status = "failed"`
- No subscription created
- No earnings entry created
- Error logged but payment verification continues

**Key Code Locations**:
- Webhook: `backend/payment_lambda/app/services/payment_service.py` lines 422-498
- Verification: `backend/payment_lambda/app/services/payment_service.py` lines 133-303

---

## 3. Verification Logic

### Payment ID Validation
- **Location**: `backend/payment_lambda/app/services/payment_service.py` lines 150-152
- Validates `payment_id` is not empty before making Razorpay API call

### Razorpay Payment Verification
- **Location**: `backend/payment_lambda/app/services/payment_service.py` line 155
- Fetches payment details from Razorpay: `razorpay_client.payment.fetch(payment_id)`
- Extracts: `order_id`, `status`, `amount`

### Amount Validation (if plan_id provided)
- **Location**: `backend/payment_lambda/app/services/payment_service.py` lines 161-193
- Fetches plan from database using `plan_id`
- Compares payment amount with plan price from database
- Allows 1 paisa difference for floating point precision
- Raises HTTPException 400 if amounts don't match

### Webhook Signature Verification
- **Location**: `backend/payment_lambda/app/services/payment_service.py` lines 444-456
- Uses HMAC SHA256 with `WEBHOOK_SECRET`
- Compares generated signature with `x-razorpay-signature` header
- Raises HTTPException 400 if signatures don't match

### Idempotency Checks
- **Subscription Check**: Before creating subscription, checks if one already exists for `payment_id` using GSI `GSI_PaymentSubscription`
- **Location**: `backend/payment_lambda/app/services/payment_service.py` lines 230-235

**Key Code Locations**:
- Payment ID validation: lines 150-152
- Razorpay verification: line 155
- Amount validation: lines 161-193
- Signature verification: lines 444-456
- Idempotency: lines 230-235

---

## 4. DynamoDB Payment Table Schema

### Table Name
`PaymentsTable`

### Schema Definition
**Location**: `backend/template.yaml` lines 234-244

```yaml
PaymentsTable:
  Type: AWS::DynamoDB::Table
  Properties:
    TableName: PaymentsTable
    AttributeDefinitions:
      - AttributeName: payment_id
        AttributeType: S # String (Partition Key)
    KeySchema:
      - AttributeName: payment_id
        KeyType: HASH # Partition Key
    BillingMode: PAY_PER_REQUEST
```

### Attributes Stored
**Location**: `backend/payment_lambda/app/services/payment_service.py` lines 202-218

```python
payment_data = {
    "payment_id": payment_id,           # Primary Key (Partition Key)
    "order_id": order_id,                # Razorpay order ID
    "amount": amount_paid,               # Decimal amount in INR
    "currency": "INR",                   # Currency code
    "status": status,                    # "captured", "failed", etc.
    "timestamp": str(datetime.datetime.utcnow()),  # UTC timestamp string
    "invoice_url": invoice_url,          # Generated invoice URL (if captured)
    # Optional fields (if provided):
    "user_id": user_id,                  # User who made payment
    "doctor_id": doctor_id,              # Doctor associated with payment
    "plan_id": plan_id                   # Plan identifier
}
```

### Key Points
- **Primary Key**: `payment_id` (Partition Key only, no Sort Key)
- **Billing Mode**: PAY_PER_REQUEST (on-demand)
- **No Global Secondary Indexes** defined on PaymentsTable
- All attributes are stored as-is (no enforced schema beyond primary key)

---

## 5. Payment Status Storage and Updates

### Where Status is Stored
- **Table**: `PaymentsTable` (DynamoDB)
- **Primary Key**: `payment_id`
- **Status Field**: `status` (string attribute)

### Status Values
- `"captured"`: Payment successful
- `"failed"`: Payment failed
- Other Razorpay statuses as returned by Razorpay API

### When Status is Updated

#### 1. Webhook Handler (Automatic)
- **Location**: `backend/payment_lambda/app/services/payment_service.py` lines 422-498
- **Trigger**: Razorpay sends webhook on `payment.captured` event
- **Action**: Calls `verify_payment()` which stores/updates payment record
- **Status Source**: Razorpay payment details (`payment_details.get("status")`)

#### 2. Manual Verification (If Exposed)
- **Location**: `backend/payment_lambda/app/services/payment_service.py` lines 133-303
- **Function**: `verify_payment()`
- **Status Source**: Razorpay payment details

### Update Process
1. Fetch payment details from Razorpay: `razorpay_client.payment.fetch(payment_id)`
2. Extract status: `status = payment_details.get("status", "failed")`
3. Store/update in DynamoDB: `PAYMENTS_TABLE.put_item(Item=payment_data)`
4. **Note**: Uses `put_item()` which overwrites existing record if `payment_id` exists

### Status-Dependent Actions
**If `status == "captured"`**:
- Generates invoice URL
- Creates subscription (if metadata provided)
- Creates earnings ledger entry (if doctor_id provided)

**If `status == "failed"`**:
- Only payment record is stored
- No subscription created
- No earnings entry created

**Key Code Locations**:
- Status extraction: `backend/payment_lambda/app/services/payment_service.py` line 157
- Status storage: `backend/payment_lambda/app/services/payment_service.py` line 221
- Status-dependent logic: lines 197-268

---

## Summary

- **Order Creation**: Frontend calls `/process-payment/payment-link` → Backend fetches plan from DB → Creates Razorpay payment link → Returns short_url
- **Success/Failure**: Handled via Razorpay webhook → Signature verified → Payment verified → Status stored in DynamoDB → Subscription/earnings created if successful
- **Verification**: Payment ID validation → Razorpay API verification → Amount validation (if plan_id) → Webhook signature verification → Idempotency checks
- **Schema**: `PaymentsTable` with `payment_id` as partition key, stores payment details including status
- **Status Updates**: Stored/updated in `PaymentsTable.status` field via webhook handler or manual verification
