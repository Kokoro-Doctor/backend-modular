# ABHA Lambda

ABDM (Ayushman Bharat Digital Mission) integration: ABHA creation/login, profile & card, HIP-initiated record linking, and inbound ABDM webhooks. Routes are deployed on the ABHA Lambda (`/abha/*` and fixed ABDM callback paths).

**Token model:** ABDM user tokens are stored in the **AbhaAccounts** DynamoDB table by the backend. The frontend does **not** send `X-ABHA-Token`. After create/login verify, tokens are persisted server-side keyed by `abha_number`. Profile and card look up the stored ABDM token directly by `abha_number` (passed as a query param) and auto-refresh if expired. **No Kokoro JWT is required on any ABHA endpoint.**

**Multi-hospital support (Flow D):** Kokoro acts as an HRP (Health Record Provider / bridge) for multiple hospitals. Each hospital has a unique `hip_id` (ABDM service ID) stored in the **HospitalAbdmConfig** DynamoDB table. All HIP-initiated linking calls must provide `hospital_id` (Kokoro's internal hospital UUID) so the correct `X-HIP-ID` header is used when calling ABDM. Link tokens are scoped per hospital — a patient can have active link tokens from multiple hospitals simultaneously.

**Async transaction tracking:** HIP linking APIs are callback-based. Each outbound call (`generate-token`, `care-context`) writes a **PENDING** row to the **AbdmTransactions** DynamoDB table keyed by `request_id` (the `REQUEST-ID` sent to ABDM). Webhooks flip the row to **COMPLETED** or **FAILED** using `response.requestId` from the callback body (not the callback's `REQUEST-ID` header). Use **`GET /abha/transactions`** to inspect status and payloads.

**Flows:**

- **Flow A** — Create new ABHA via Aadhaar OTP (`POST /abha/create/*`)
- **Flow B** — Login with existing ABHA number (`POST /abha/login/*`)
- **Flow C** — Profile & card (`GET /abha/profile`, `GET /abha/card`) — **`abha_number` query param required, no auth**
- **Flow D** — HIP-initiated linking (`POST /abha/link/*`, bridge admin, `GET /abha/transactions`)
- **Flow E** — ABDM → Kokoro webhooks (inbound callbacks; paths fixed by ABDM spec)

---

## Flow A — Create ABHA (Aadhaar)

### POST `/abha/create/request-otp`

Step 1: Request OTP on the mobile linked to Aadhaar. No auth required.

**Auth required:** No

**Request body:**
```json
{
  "aadhaar": "123456789012"
}
```

**Postman setup:**
- **Method:** POST
- **URL:** `{{base_url}}/abha/create/request-otp`
- **Headers:** None required
- **Body (raw JSON):**
```json
{
  "aadhaar": "123456789012"
}
```

**Success response (200):**
```json
{
  "txn_id": "abc123-txn-id-from-abdm",
  "message": "OTP sent to mobile number ending with XXXX"
}
```

**Error responses:**
- `400` — Invalid Aadhaar format or Aadhaar not linked to mobile
- `500` — ABDM service error

**Important notes:**
- Aadhaar is encrypted server-side before calling ABDM
- Use `txn_id` from response in the next step
- OTP is sent to the mobile number registered with ABDM/Aadhaar

---

### POST `/abha/create/verify-otp`

Step 2: Verify OTP and create or retrieve the ABHA account. Saves profile + tokens to **AbhaAccounts** keyed by `abha_number`.

**Auth required:** None

**Postman setup:**
- **Method:** POST
- **URL:** `{{base_url}}/abha/create/verify-otp`
- **Headers:** None required
- **Body (raw JSON):**
```json
{
  "txn_id": "abc123-txn-id-from-abdm",
  "otp": "123456",
  "mobile": "9587733170"
}
```

**Success response (200):**
```json
{
  "message": "ABHA created successfully",
  "txn_id": "abc123-txn-id-from-abdm",
  "is_new": true,
  "abha_number": "12-3456-7890-1234",
  "abha_profile": {
    "ABHANumber": "12-3456-7890-1234",
    "firstName": "John",
    "lastName": "Doe",
    "mobile": "9587733170",
    "gender": "M",
    "dob": "1990-01-15"
  },
  "tokens": {
    "token": "eyJ...",
    "expiresIn": 1800,
    "refreshToken": "eyJ...",
    "refreshExpiresIn": 1296000
  }
}
```

**Error responses:**
- `400` — Invalid OTP, txn_id not found, or OTP expired
- `500` — Database or ABDM service error

**Important notes:**
- `txn_id` must be from the previous `/create/request-otp` call
- **Save the `abha_number` from the response** — you'll need it for `/abha/profile` and `/abha/card`
- `tokens` are returned for client visibility but the backend stores and auto-refreshes them

---

## Flow B — Login with existing ABHA

### POST `/abha/login/request-otp`

Step 1: Request OTP for an existing ABHA number. No auth required.

**Auth required:** No

**Request body:**
```json
{
  "abha_number": "12-3456-7890-1234"
}
```

**Postman setup:**
- **Method:** POST
- **URL:** `{{base_url}}/abha/login/request-otp`
- **Headers:** None required
- **Body (raw JSON):**
```json
{
  "abha_number": "12-3456-7890-1234"
}
```

**Success response (200):**
```json
{
  "txn_id": "def456-txn-id-from-abdm",
  "message": "OTP sent successfully"
}
```

**Error responses:**
- `400` — Invalid ABHA number format
- `404` — ABHA number not found in ABDM
- `500` — ABDM service error

**Important notes:**
- ABHA number format: `XX-XXXX-XXXX-XXXX` (12 digits with hyphens)
- Use `txn_id` from response in the next step

---

### POST `/abha/login/verify-otp`

Step 2: Verify OTP and log in to ABHA. Saves profile + tokens to **AbhaAccounts** keyed by `abha_number`.

**Auth required:** None

**Postman setup:**
- **Method:** POST
- **URL:** `{{base_url}}/abha/login/verify-otp`
- **Headers:** None required
- **Body (raw JSON):**
```json
{
  "txn_id": "def456-txn-id-from-abdm",
  "otp": "123456"
}
```

**Success response (200):**
```json
{
  "message": "Login verified",
  "abha_number": "12-3456-7890-1234",
  "abha_profile": {
    "ABHANumber": "12-3456-7890-1234",
    "firstName": "John",
    "lastName": "Doe",
    "mobile": "9587733170",
    "gender": "M"
  },
  "tokens": {
    "token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
    "expiresIn": 1800,
    "refreshToken": "eyJ0eXAiOiJKV1QiLCJhbGc...",
    "refreshExpiresIn": 1296000
  }
}
```

**Error responses:**
- `400` — Invalid OTP or txn_id not found
- `500` — Database or ABDM service error

**Important notes:**
- `txn_id` must be from the previous `/login/request-otp` call
- **Save the `abha_number` from the response** — you'll need it for `/abha/profile` and `/abha/card`
- Token expiry: 30 minutes for access token, 15 days for refresh token

---

## Flow C — Profile & card

Both endpoints use `abha_number` as a query parameter — no auth needed. The backend:
1. Receives `abha_number` from query param
2. Looks up stored ABDM token directly from **AbhaAccounts** table (by PK)
3. Auto-refreshes the token if expired
4. Calls ABDM and returns fresh data

**Auth required:** None

---

### GET `/abha/profile`

Fetch live ABHA profile from ABDM.

**Postman setup:**
- **Method:** GET
- **URL:** `{{base_url}}/abha/profile?abha_number={{abha_number}}`
- **Headers:** None required
- **Body:** (none)

**Example URL:**
```
GET {{base_url}}/abha/profile?abha_number=12-3456-7890-1234
```

**Success response (200):**
```json
{
  "abha_profile": {
    "ABHANumber": "12-3456-7890-1234",
    "preferredAbhaAddress": "john.doe@abdm",
    "firstName": "John",
    "lastName": "Doe",
    "mobile": "9587733170",
    "gender": "M",
    "dob": "1990-01-15",
    "abhaStatus": "ACTIVE"
  }
}
```

**Error responses:**
- `400` — `abha_number` query param missing
- `404` — No ABHA record found for this `abha_number` (run create or login first)
- `401` — Stored ABDM tokens expired (user must OTP again via create/login)
- `500` — ABDM service error

**Important notes:**
- Returns **live data from ABDM**, not cached data
- `abha_number` must have been saved via `/create/verify-otp` or `/login/verify-otp` first
- All profile fields are read-only from ABDM

---

### GET `/abha/card`

Download ABHA card as a PDF (returned as Base64).

**Postman setup:**
- **Method:** GET
- **URL:** `{{base_url}}/abha/card?abha_number={{abha_number}}`
- **Headers:** None required
- **Body:** (none)

**Example URL:**
```
GET {{base_url}}/abha/card?abha_number=12-3456-7890-1234
```

**Success response (200):**
```json
{
  "card_base64": "JVBERi0xLjQKJeLjz9MNCjEgMCBvYmogICUgRW50cnkgcG9pbnQKPDwgL1R5cGUgL0NhdGFsb2cgL1BhZ2VzIDIgMCBSID4+CmVuZG9iagoyIDAgb2JqCjw8IC9UeXBlIC9QYWdlcyAvS2lkcyBbMyAwIFJdIC9Db3VudCAxID4+CmVuZG9iag..."
}
```

**Error responses:**
- `400` — `abha_number` query param missing
- `404` — No ABHA record found for this `abha_number`
- `401` — Stored ABDM tokens expired (user must OTP again)
- `500` — ABDM service error

**Important notes:**
- `card_base64` is a complete PDF file encoded as Base64
- **To decode in terminal:** `echo 'JVBERi...' | base64 -d > card.pdf && open card.pdf`
- The PDF contains the official ABHA card with QR code

---

## Flow D — HIP-initiated linking (Milestone 2)

**Overview:** Hospital initiates record linking with ABDM. All calls are **asynchronous**:
1. Backend calls ABDM with `REQUEST-ID` header
2. Returns **200** with `request_id` immediately (for tracking)
3. ABDM processes async and POSTs callback to your webhook (`/api/v3/hip/...`)
4. Webhook updates **AbdmTransactions** table

**Key sequence:**
1. **Register hospital** via `POST /abha/bridge/register-facility` (admin, once per hospital)
2. **Generate token** via `POST /abha/link/generate-token` → wait for webhook
3. **Link care contexts** via `POST /abha/link/care-context` → wait for webhook
4. **Monitor status** via `GET /abha/transactions?request_id=<id>`

**Auth required for all Flow D routes:** None — no auth headers are checked by any endpoint in this flow

---

### POST `/abha/link/generate-token`

Request a link token for a patient. ABDM sends the token back to your webhook `/api/v3/hip/token/on-generate-token`. Provide **either** `abha_address` **or** `abha_number`, not both.

**Auth required:** None

**Postman setup (using ABHA address):**
- **Method:** POST
- **URL:** `{{base_url}}/abha/link/generate-token`
- **Headers:**
```
Content-Type: application/json
```
- **Body (raw JSON):**
```json
{
  "hospital_id": "hosp-uuid-123",
  "abha_address": "john.doe@abdm",
  "name": "John Doe",
  "gender": "M",
  "year_of_birth": 1990
}
```

**Postman setup (using ABHA number instead):**
```json
{
  "hospital_id": "hosp-uuid-456",
  "abha_number": "12-3456-7890-1234",
  "name": "John Doe",
  "gender": "F",
  "year_of_birth": 1985
}
```

**Success response (200):**
```json
{
  "message": "Link token generation request accepted. Token will be available shortly.",
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "hospital_id": "hosp-uuid-123",
  "abha_address": "john.doe@abdm",
  "abha_number": null
}
```

**Error responses:**
- `400` — Neither `abha_address` nor `abha_number` provided
- `404` — `hospital_id` not found (register hospital first)
- `403` — Hospital ABDM status is `inactive`
- `500` — ABDM service error

**Important notes:**
- `hospital_id` must be registered first via `POST /abha/bridge/register-facility`
- Gender values: `M`, `F`, `O` (other)
- `year_of_birth` must be a valid year (e.g., 1990)
- **Keep the `request_id` to track this request**
- Poll `GET /abha/transactions?request_id=<request_id>` to check when ABDM responds
- Link token arrives at your webhook and is auto-stored in DB
- Link token is **hospital-scoped** — use same `hospital_id` for next step

---

### POST `/abha/link/care-context`

Link care contexts (medical records) to a patient's ABHA for a specific hospital. Requires a valid link token from the previous `generate-token` call.

**Auth required:** None

**Postman setup:**
- **Method:** POST
- **URL:** `{{base_url}}/abha/link/care-context`
- **Headers:**
```
Content-Type: application/json
```
- **Body (raw JSON):**
```json
{
  "hospital_id": "hosp-uuid-123",
  "abha_address": "john.doe@abdm",
  "abha_number": "12-3456-7890-1234",
  "patient": [
    {
      "referenceNumber": "PAT-001",
      "display": "John Doe",
      "hiType": "OPConsultation",
      "count": 1,
      "careContexts": [
        {
          "referenceNumber": "CC-2025-001",
          "display": "OPD Consultation on 15 Jan 2025"
        },
        {
          "referenceNumber": "CC-2025-002",
          "display": "Blood Work on 20 Jan 2025"
        }
      ]
    }
  ]
}
```

**Success response (200):**
```json
{
  "message": "Care context linking request accepted.",
  "request_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
  "hospital_id": "hosp-uuid-123",
  "abha_address": "john.doe@abdm"
}
```

**Error responses:**
- `409` — No link token for this patient at this hospital (call `/link/generate-token` first)
- `404` — Hospital not registered or `abha_number` cannot be resolved from `abha_address`
- `403` — Hospital ABDM registration is `inactive`
- `400` — Invalid patient/care context data structure
- `500` — ABDM service error

**Important notes:**
- **MUST use the same `hospital_id` as in the `/link/generate-token` call**
- Provide either `abha_address` or both `abha_address` and `abha_number`
- `hiType` values: `PRESCRIPTION`, `DiagnosticReport`, `OPConsultation`, `LabReport`, `DischargeSummary`, etc.
- `count` = number of care contexts for this health info type
- Can link multiple care contexts in one call
- **Keep the `request_id` to track this request**
- Poll `GET /abha/transactions?request_id=<request_id>` to check ABDM response
- Success callback arrives at `/api/v3/link/on_carecontext`

---

### PATCH `/abha/bridge/url`

**Admin setup (one-time per environment).** Register Kokoro's API base URL as the ABDM bridge callback endpoint. ABDM will POST all async callbacks to `{url}/api/v3/hip/...` and `{url}/api/v3/link/...`.

**Auth required:** None

**Postman setup:**
- **Method:** PATCH
- **URL:** `{{base_url}}/abha/bridge/url`
- **Headers:**
```
Content-Type: application/json
```
- **Body (raw JSON):**
```json
{
  "url": "https://api.example.com"
}
```

**Success response (200):**
```json
{
  "message": "Bridge URL updated to https://api.example.com"
}
```

**Error responses:**
- `400` — Invalid or malformed URL
- `500` — Database error

**Important notes:**
- Call this **once per environment** (dev/staging/prod)
- ABDM will use this URL as base and POST to: `{url}/api/v3/hip/token/on-generate-token`, `{url}/api/v3/link/on_carecontext`, etc.
- Must be an HTTPS URL accessible from ABDM systems
- Store in **BridgeConfig** DynamoDB table

---

### POST `/abha/bridge/register-facility`

**Admin setup (once per hospital).** Register a health facility with ABDM and store config in Kokoro. This creates the hospital's ABDM bridge credentials.

**Auth required:** None

**Postman setup:**
- **Method:** POST
- **URL:** `{{base_url}}/abha/bridge/register-facility`
- **Headers:**
```
Content-Type: application/json
```
- **Body (raw JSON):**
```json
{
  "hospital_id": "hosp-uuid-789",
  "facility_id": "IN2810014366",
  "facility_name": "City Hospital",
  "bridge_id": "SBX_KOKORO",
  "hip_name": "CITYHOSPITAL01",
  "service_type": "HIP",
  "active": true
}
```

**Success response (200):**
```json
{
  "message": "Facility IN2810014366 registered successfully.",
  "hospital_id": "hosp-uuid-789",
  "hip_id": "CITYHOSPITAL01"
}
```

**Error responses:**
- `400` — Invalid data or `hip_name` already exists
- `500` — ABDM registration error

**Field reference:**
| Field | Description | Example | Notes |
|-------|-------------|---------|-------|
| `hospital_id` | Kokoro's internal hospital UUID | `hosp-uuid-789` | Must be unique within Kokoro |
| `facility_id` | HFR ID from ABDM | `IN2810014366` | ABDM-issued, from registration documents |
| `facility_name` | Hospital/clinic display name | `City Hospital` | Human-readable, for logging |
| `bridge_id` | Kokoro's ABDM bridge identifier | `SBX_KOKORO` | Dev/staging: SBX_KOKORO, Prod: varies |
| `hip_name` | ABDM service ID / X-HIP-ID | `CITYHOSPITAL01` | ≤15 chars, alphanumeric, **unique per facility** |
| `service_type` | Service type | `HIP` | Usually `HIP` |
| `active` | Enable/disable | `true` | Set to `false` to deactivate |

**Important notes:**
- **Save `hospital_id` and `hip_name`** — use `hospital_id` in all subsequent Flow D calls
- `hip_name` becomes the `X-HIP-ID` header sent to ABDM in all HIP calls
- Must be ≤15 characters, alphanumeric only (no spaces or special chars)
- Once registered, you can call `/link/generate-token` and `/link/care-context` with this `hospital_id`
- Stored in **HospitalAbdmConfig** DynamoDB table

---

### GET `/abha/transactions`

**Admin monitoring.** Inspect async ABDM request/callback state from **AbdmTransactions** table. Use to confirm ABDM responses or debug stuck requests.

**Auth required:** None

**Query parameters (all optional):**

| Param | Type | Description | Example |
|-------|------|-------------|---------|
| `request_id` | string | Get single transaction with full details | `a1b2c3d4-e5f6-7890-abcd-ef1234567890` |
| `hip_id` | string | Filter by hospital (uses GSI, newest first) | `CITYHOSPITAL01` |
| `status` | string | Filter: `PENDING` \| `COMPLETED` \| `FAILED` | `PENDING` |
| `limit` | int | Max rows to return (default: 50, max: 100) | `20` |

**Postman examples:**

Get single transaction by request_id:
```
GET {{base_url}}/abha/transactions?request_id=a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

List all pending requests for a hospital:
```
GET {{base_url}}/abha/transactions?hip_id=CITYHOSPITAL01&status=PENDING
```

List all failed transactions (last 20):
```
GET {{base_url}}/abha/transactions?status=FAILED&limit=20
```

List recent transactions (default):
```
GET {{base_url}}/abha/transactions?limit=50
```

**Success response (single transaction):**
```json
{
  "transaction": {
    "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "api": "generate-token",
    "hospital_id": "hosp-uuid-123",
    "hip_id": "CITYHOSPITAL01",
    "abha_address": "john.doe@abdm",
    "status": "COMPLETED",
    "request_payload": {
      "name": "John Doe",
      "gender": "M",
      "yearOfBirth": 1990,
      "abhaAddress": "john.doe@abdm"
    },
    "callback_payload": {
      "abhaAddress": "john.doe@abdm",
      "linkToken": "eyJ0eXAiOiJKV1QiLCJhbGc...",
      "response": {
        "requestId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
      }
    },
    "callback_received_at": "2025-05-27T10:16:30.000Z",
    "created_at": "2025-05-27T10:15:00.000Z",
    "updated_at": "2025-05-27T10:16:30.000Z"
  }
}
```

**Success response (list):**
```json
{
  "transactions": [
    {
      "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "api": "generate-token",
      "hospital_id": "hosp-uuid-123",
      "hip_id": "CITYHOSPITAL01",
      "status": "COMPLETED",
      "created_at": "2025-05-27T10:15:00.000Z"
    },
    {
      "request_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
      "api": "link-carecontext",
      "hospital_id": "hosp-uuid-456",
      "hip_id": "CITYHOSPITAL02",
      "status": "PENDING",
      "created_at": "2025-05-27T10:20:00.000Z"
    }
  ],
  "count": 2
}
```

**Error responses:**
- `404` — `request_id` not found
- `500` — Database error

**Status reference:**
- `PENDING` — Request sent to ABDM, waiting for callback
- `COMPLETED` — ABDM callback received successfully
- `FAILED` — ABDM callback received with error

**Important notes:**
- `api` values: `generate-token` or `link-carecontext`
- Row stuck in `PENDING` = webhook not received or correlation failed
- `callback_received_at` shows when webhook arrived (in COMPLETED/FAILED rows)
- Use `request_id` from `/link/generate-token` or `/link/care-context` responses to track requests
- Check `callback_payload` to see ABDM's response (token, error code, etc.)

---

### GET `/abha/bridge/hospitals`

**Admin view.** List all registered hospitals and their ABDM config.

**Auth required:** None

**Postman setup:**
- **Method:** GET
- **URL:** `{{base_url}}/abha/bridge/hospitals`
- **Headers:** None required
- **Body:** (none)

**Success response (200):**
```json
{
  "hospitals": [
    {
      "hospital_id": "hosp-uuid-123",
      "facility_id": "IN2810014366",
      "facility_name": "City Hospital",
      "bridge_id": "SBX_KOKORO",
      "hip_id": "CITYHOSPITAL01",
      "hip_name": "CITYHOSPITAL01",
      "abdm_status": "registered",
      "created_at": "2025-05-27T10:15:00.000Z",
      "updated_at": "2025-05-27T10:15:00.000Z"
    },
    {
      "hospital_id": "hosp-uuid-456",
      "facility_id": "IN2810014367",
      "facility_name": "Apollo Hospital",
      "bridge_id": "SBX_KOKORO",
      "hip_id": "APOLLOHOSPITAL",
      "hip_name": "APOLLOHOSPITAL",
      "abdm_status": "registered",
      "created_at": "2025-05-28T14:20:00.000Z",
      "updated_at": "2025-05-28T14:20:00.000Z"
    }
  ],
  "count": 2
}
```

**Error responses:**
- `500` — Database error

**Field reference:**
| Field | Description |
|-------|-------------|
| `hospital_id` | Kokoro's internal hospital UUID (use this in `/link/*` calls) |
| `facility_id` | ABDM HFR facility ID |
| `facility_name` | Hospital display name |
| `bridge_id` | Kokoro's ABDM bridge ID |
| `hip_id` | ABDM service ID (used as X-HIP-ID header) |
| `hip_name` | Same as hip_id |
| `abdm_status` | Current registration status with ABDM |
| `created_at` | Registration timestamp |
| `updated_at` | Last modification timestamp |

**Useful for:**
- Verifying hospital registration before calling `/link/*` endpoints
- Debugging X-HIP-ID issues
- Finding hospital_id to use in linking flows
- Checking which hospitals are active

---

## Flow E — ABDM webhooks (inbound)

**Receiving callbacks from ABDM.** After you call `/link/generate-token` or `/link/care-context`, ABDM processes async and POSTs results back to these endpoints. Paths are **fixed by ABDM spec** (no `/abha` prefix) and registered via `PATCH /abha/bridge/url`.

**These are NOT meant to be called from Postman.** They are inbound callbacks from ABDM's servers. This section documents what ABDM sends for reference and testing.

---

### Webhook headers (from ABDM)

All ABDM callbacks include these headers:

```
Authorization: Bearer <abdm_gateway_jwt>
X-HIP-ID: <hip_id>
REQUEST-ID: <new-uuid-for-this-delivery>
X-CM-ID: sbx (or prod)
TIMESTAMP: <ISO 8601 timestamp>
```

**Header reference:**
| Header | Description |
|--------|-------------|
| `Authorization` | ABDM's JWT for verifying callback authenticity |
| `X-HIP-ID` | Hospital's service ID — identifies which hospital this callback belongs to |
| `REQUEST-ID` | Unique ID for this delivery (different from original request) |
| `X-CM-ID` | Environment: `sbx` (sandbox) or `prod` |
| `TIMESTAMP` | When ABDM sent the callback |

**Correlation logic:**
- ABDM echoes your original `REQUEST-ID` in the **JSON body** as `response.requestId`
- Use `response.requestId` to match callbacks to your original requests
- The `REQUEST-ID` **header** is just for this delivery and changes on retries

---

### POST `/api/v3/hip/token/on-generate-token`

Link token callback from ABDM (ABDM spec 4.3.2). ABDM sends the link token after your `/link/generate-token` request succeeds.

**Expected callback body (from ABDM):**
```json
{
  "abhaAddress": "john.doe@abdm",
  "linkToken": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "response": {
    "requestId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
  }
}
```

**What Kokoro does:**
1. Receives POST from ABDM
2. Validates `X-HIP-ID` header
3. Stores `linkToken` in **AbhaAccounts** table, keyed by `abhaAddress` + `hip_id` (hospital-scoped)
4. Updates **AbdmTransactions** row to `COMPLETED` (matched by `response.requestId`)
5. Returns `{}` with HTTP `202`

**Response Kokoro sends:**
```
HTTP 202 Accepted

{}
```

**Testing note:** You cannot test this directly from Postman. ABDM must POST to your registered bridge URL.

**Troubleshooting:**
- Check `GET /abha/transactions?request_id=<original-request-id>` to see if callback was received
- If still `PENDING` after waiting, the webhook may not have arrived
- Verify `PATCH /abha/bridge/url` was called to register your callback endpoint

---

### POST `/api/v3/link/on_carecontext`

Care context linking result from ABDM (ABDM spec 4.3.4). ABDM confirms success or failure of your `/link/care-context` request.

**Expected callback body (success):**
```json
{
  "abhaAddress": "john.doe@abdm",
  "status": "Successfully Linked care context",
  "response": {
    "requestId": "b2c3d4e5-f6a7-8901-bcde-f12345678901"
  }
}
```

**Expected callback body (error):**
```json
{
  "abhaAddress": "john.doe@abdm",
  "error": {
    "code": "ABDM-1056",
    "message": "This care context has been already linked"
  },
  "response": {
    "requestId": "b2c3d4e5-f6a7-8901-bcde-f12345678901"
  }
}
```

**What Kokoro does:**
1. Receives POST from ABDM
2. Validates `X-HIP-ID` header (hospital routing)
3. If `status` = "Successfully Linked..." → updates **AbdmTransactions** to `COMPLETED`
4. If `error` present → updates **AbdmTransactions** to `FAILED`, stores error details
5. Returns `{}` with HTTP `202`

**Response Kokoro sends:**
```
HTTP 202 Accepted

{}
```

**Common error codes:**
| Code | Meaning | Action |
|------|---------|--------|
| `ABDM-1056` | Care context already linked | Check if already linked in ABDM |
| `ABDM-1050` | Patient not found | Verify ABHA address/number |
| `ABDM-1055` | Invalid care context format | Check patient/careContext structure |

**Testing note:** Like the token callback, you cannot test this directly from Postman.

**Troubleshooting:**
- Check `GET /abha/transactions?request_id=<care-context-request-id>` to see callback result
- Look at `callback_payload` to see full error response from ABDM
- If still `PENDING`, webhook may not have arrived — check bridge URL registration

---

## Postman Testing Guide

### Environment variables to set

Create environment variables in Postman for easy testing. Go to **Environment > Manage Environments > Edit** and add:

| Variable | Example Value | Purpose |
|----------|---------------|---------|
| `base_url` | `http://localhost:8000` | Backend base URL (dev/staging/prod) |
| `aadhaar` | `123456789012` | Test Aadhaar number |
| `mobile` | `9587733170` | Mobile linked to Aadhaar |
| `abha_number` | `12-3456-7890-1234` | ABHA number (save from create/login response) |
| `abha_address` | `john.doe@abdm` | ABHA address (from profile response) |
| `hospital_id` | `hosp-uuid-123` | Hospital UUID (from register-facility response) |
| `hip_id` | `CITYHOSPITAL01` | Hospital's ABDM service ID |
| `txn_id` | (auto-set by test script) | Transaction ID from request-otp |
| `request_id` | (auto-set by test script) | Request ID from linking calls |

**No auth headers needed anywhere.** All endpoints are open.

Then reference in requests with `{{variable_name}}`.

---

### Recommended test sequence

**Setup Phase (run once per environment)**

1. **Register bridge callback URL**
   ```
   PATCH {{base_url}}/abha/bridge/url
   Body: {"url": "https://your-deployed-api.com"}
   ```
   - Tells ABDM where to send async callbacks

2. **Register hospital facility** (no auth needed)
   ```
   POST {{base_url}}/abha/bridge/register-facility
   Headers: Content-Type: application/json
   Body: {
     "hospital_id": "hosp-uuid-789",
     "facility_id": "IN2810014366",
     "facility_name": "City Hospital",
     "bridge_id": "SBX_KOKORO",
     "hip_name": "CITYHOSPITAL01",
     "service_type": "HIP",
     "active": true
   }
   ```
   - **Save the `hospital_id` and `hip_id` to environment variables**

3. **Verify hospitals**
   ```
   GET {{base_url}}/abha/bridge/hospitals
   ```
   - Confirm hospital is registered

**User Flow Phase (Flow A — Create ABHA)**

1. **Request OTP** (no auth needed)
   ```
   POST {{base_url}}/abha/create/request-otp
   Headers: Content-Type: application/json
   Body: {"aadhaar": "{{aadhaar}}"}
   ```
   - Response: `{txn_id: "...", message: "OTP sent..."}`
   - Use Test script to auto-save: `pm.environment.set("txn_id", pm.response.json().txn_id);`

2. **Verify OTP and create ABHA** (no auth)
   ```
   POST {{base_url}}/abha/create/verify-otp
   Headers: Content-Type: application/json
   Body: {
     "txn_id": "{{txn_id}}",
     "otp": "123456",
     "mobile": "{{mobile}}"
   }
   ```
   - Response includes ABHA number, profile, tokens
   - **Save `abha_number` from the response** → set `{{abha_number}}` env variable

3. **Fetch live profile**
   ```
   GET {{base_url}}/abha/profile?abha_number={{abha_number}}
   ```
   - No headers needed
   - Shows current ABHA profile from ABDM

4. **Download ABHA card as PDF**
   ```
   GET {{base_url}}/abha/card?abha_number={{abha_number}}
   ```
   - No headers needed
   - Response: `{card_base64: "JVBERi..."}`
   - Decode: `echo 'JVBERi...' | base64 -d > card.pdf` (Mac/Linux)

**Linking Flow Phase (Flow D — HIP-initiated)**

1. **Generate link token** (async)
   ```
   POST {{base_url}}/abha/link/generate-token
   Headers: Content-Type: application/json
   Body: {
     "hospital_id": "{{hospital_id}}",
     "abha_address": "{{abha_address}}",
     "name": "John Doe",
     "gender": "M",
     "year_of_birth": 1990
   }
   ```
   - Response: `{request_id: "...", message: "...accepted..."}`
   - **Save request_id**: `pm.environment.set("request_id", pm.response.json().request_id);`
   - ABDM processes async and POSTs to webhook

2. **Poll for token completion** (wait 5-10 seconds first)
   ```
   GET {{base_url}}/abha/transactions?request_id={{request_id}}
   ```
   - Keep polling until `status` = `COMPLETED`
   - Check `callback_payload.linkToken` when ready

3. **Link care contexts** (uses stored link token)
   ```
   POST {{base_url}}/abha/link/care-context
   Headers: Content-Type: application/json
   Body: {
     "hospital_id": "{{hospital_id}}",
     "abha_address": "{{abha_address}}",
     "abha_number": "{{abha_number}}",
     "patient": [
       {
         "referenceNumber": "PAT-001",
         "display": "John Doe",
         "hiType": "OPConsultation",
         "count": 1,
         "careContexts": [
           {
             "referenceNumber": "CC-2025-001",
             "display": "OPD Visit on 15 Jan 2025"
           }
         ]
       }
     ]
   }
   ```
   - Response: `{request_id: "...", message: "...accepted..."}`
   - Save request_id for tracking

4. **Poll for linking completion** (wait 5-10 seconds)
   ```
   GET {{base_url}}/abha/transactions?request_id={{request_id}}
   ```
   - Poll until `status` = `COMPLETED` or `FAILED`
   - On FAILED: check `callback_payload.error` for reason

**Monitoring Phase**

```
GET {{base_url}}/abha/transactions?hip_id={{hip_id}}&status=PENDING&limit=10
```
- See pending requests for your hospital

```
GET {{base_url}}/abha/transactions?status=FAILED&limit=5
```
- See recent failures for debugging

---

### Postman automation tips

**Auto-extract IDs into environment variables**

Add this to the **Tests** tab of `/create/request-otp`:
```javascript
var jsonData = pm.response.json();
pm.environment.set("txn_id", jsonData.txn_id);
pm.test("txn_id saved", () => pm.expect(jsonData.txn_id).to.be.a("string"));
```

**Add validation tests**

In any response test:
```javascript
pm.test("Status is 200", () => pm.response.to.have.status(200));
pm.test("Response has request_id", () => {
    pm.expect(pm.response.json()).to.have.property("request_id");
});
pm.test("request_id is UUID", () => {
    const uuid = pm.response.json().request_id;
    pm.expect(uuid).to.match(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i);
});
```

**Decode Base64 card to file**

In the response tab, use Pre-request Script:
```javascript
const response = pm.response.json();
const base64 = response.card_base64;
// Manually copy and paste into an online base64 decoder, save as .pdf
console.log("Copy this base64 string to decode: " + base64.substring(0, 100) + "...");
```

Or use Node.js locally:
```bash
# After copying card_base64 from Postman response:
echo 'JVBERi0xLjQK...' | base64 -d > card.pdf
open card.pdf  # on Mac
```

---

### Error handling in Postman

**401 Unauthorized**
- Stored ABDM tokens for this `abha_number` have fully expired (access + refresh both expired)
- Solution: Run create or login OTP flow again to get fresh tokens stored in DB

**404 Not found**
- Hospital not registered or ABHA not linked to user
- Solution: Run setup phase first, confirm hospital_id is correct

**409 Conflict**
- No link token for patient at this hospital
- Solution: Call `/link/generate-token` first, wait for COMPLETED status

**500 Server Error**
- Check backend logs
- Verify ABDM service is reachable
- Check that all required fields are present in request body

---
