# ABHA Lambda

ABDM (Ayushman Bharat Digital Mission) integration: ABHA creation/login, profile & card, HIP-initiated record linking, and inbound ABDM webhooks. Routes are deployed on the ABHA Lambda (`/abha/*` and fixed ABDM callback paths).

**Token model:** ABDM user tokens are stored in the **AbhaAccounts** DynamoDB table by the backend keyed by `abha_number`. Profile and card look up the stored ABDM token directly by `abha_number` (passed as a query param) and auto-refresh if expired. **No auth is required on any ABHA endpoint.**

**Multi-hospital support:** Kokoro acts as an HRP bridge for multiple hospitals. Each hospital has a unique `hip_id` stored in **HospitalAbdmConfig**. All HIP-initiated linking calls must include `hospital_id` so the correct `X-HIP-ID` header is sent to ABDM. Link tokens are hospital-scoped.

**Async transaction tracking:** HIP linking calls (`generate-token`, `care-context`) write a **PENDING** row to **AbdmTransactions** keyed by `request_id`. ABDM callbacks flip the row to **COMPLETED** or **FAILED**. Use `GET /abha/transactions` to monitor status.

---

## Phase 0 — One-Time Platform Setup

Run these once per environment (dev/staging/prod) before anything else.

> **Verification tip:** After registering a facility (step 2), use the live ABDM lookup endpoints (steps 3 and 4) to confirm ABDM accepted the registration. Use step 5 to cross-check what Kokoro has stored in DynamoDB against what ABDM shows live.

---

### 1. PATCH `/abha/bridge/url`

Register Kokoro's deployed API base URL with ABDM. ABDM will POST all async callbacks to `{url}/api/v3/hip/...` and `{url}/api/v3/link/...`.

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

- Must be an HTTPS URL accessible from ABDM systems
- ABDM will POST to `{url}/api/v3/hip/token/on-generate-token`, `{url}/api/v3/link/on_carecontext`, etc.
- Call once per environment — calling again just updates the URL

---

### 2. POST `/abha/bridge/register-facility`

Register a hospital/facility with ABDM and store its config in Kokoro. Run once per hospital onboarding.

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
  "bridge_id": "SBXID_023051",
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

- `422 HIS-1123` — Missing or invalid fields (wrong `facility_id` or `bridge_id`)
- `500` — ABDM registration error

**Field reference:**

| Field           | Description                     | Example          | Notes                                                                             |
| --------------- | ------------------------------- | ---------------- | --------------------------------------------------------------------------------- |
| `hospital_id`   | Kokoro's internal hospital UUID | `hosp-uuid-789`  | Must be unique within Kokoro                                                      |
| `facility_id`   | HFR ID from ABDM                | `IN2810014366`   | **Must be a real, HFR-registered facility ID** — test IDs will return `HIS-1123` |
| `facility_name` | Hospital/clinic display name    | `City Hospital`  | Human-readable, for logging                                                       |
| `bridge_id`     | Kokoro's ABDM bridge identifier | `SBXID_023051`   | **Must be your bridge ID** from the ABDM developer portal — others' IDs rejected  |
| `hip_name`      | ABDM service ID / X-HIP-ID      | `CITYHOSPITAL01` | ≤15 chars, alphanumeric only, **unique per bridge per facility**                  |
| `service_type`  | Service type                    | `HIP`            | Always `HIP`                                                                      |
| `active`        | Enable/disable                  | `true`           | Set to `false` to deactivate                                                      |

**What Kokoro sends to ABDM internally:**

Kokoro transforms your request into the ABDM `MutipleHRPAddUpdateServices` format before forwarding:

```json
{
  "facilityId": "IN2810014366",
  "facilityName": "City Hospital",
  "HRP": [
    {
      "bridgeId": "SBXID_023051",
      "hipName": "CITYHOSPITAL01",
      "type": "HIP",
      "active": true
    }
  ]
}
```

**Important notes:**

- **Save `hospital_id`** — use it in all Phase 2 linking calls
- `hip_name` becomes the `X-HIP-ID` header sent to ABDM on every subsequent HIP call
- `facility_id` must exist in the **HFR sandbox** (`https://facility.abdm.gov.in`) — ABDM validates it in real-time
- `bridge_id` must be the bridge assigned to your ABDM developer account
- After registering, verify with step 3 (`find-bridge`) that ABDM reflects the correct data

---

### 3. GET `/abha/bridge/find-bridge` — 3.2.6 Live ABDM lookup

Query ABDM directly for the bridge associated with a given service (HIP/HIU) ID. Returns live data — **not from DB**.

Use this after `register-facility` to confirm ABDM has the correct bridge mapping.

**Auth required:** None

**Postman setup:**

- **Method:** GET
- **URL:** `{{base_url}}/abha/bridge/find-bridge?service_id={{hip_name}}`
- **Headers:** None required
- **Body:** (none)

**Example:**

```
GET {{base_url}}/abha/bridge/find-bridge?service_id=CITYHOSPITAL01
```

**Success response (200) — raw ABDM response:**

```json
{
  "bridgeId": "SBXID_023051",
  "bridgeName": "Kokoro Bridge",
  "services": [
    {
      "id": "CITYHOSPITAL01",
      "name": "City Hospital",
      "type": "HIP",
      "active": true
    }
  ]
}
```

**Error responses:**

- `404` — Service ID not found in ABDM
- `502` — ABDM returned an unexpected error

**Query parameter:**

| Param        | Description                           | Example          |
| ------------ | ------------------------------------- | ---------------- |
| `service_id` | The ABDM service ID (= `hip_name` from step 2) | `CITYHOSPITAL01` |

---

### 4. GET `/abha/bridge/services` — 3.2.7 Live ABDM lookup

Query ABDM directly for all services (HIP/HIU) registered under a bridge ID. Returns live data — **not from DB**.

Use this to see everything registered under your bridge — useful to audit all registered hospitals.

**Auth required:** None

**Postman setup:**

- **Method:** GET
- **URL:** `{{base_url}}/abha/bridge/services?bridge_id={{bridge_id}}`
- **Headers:** None required
- **Body:** (none)

**Example:**

```
GET {{base_url}}/abha/bridge/services?bridge_id=SBXID_023051
```

**Success response (200) — raw ABDM response:**

```json
{
  "bridgeId": "SBXID_023051",
  "services": [
    {
      "id": "CITYHOSPITAL01",
      "name": "City Hospital",
      "facilityId": "IN2810014366",
      "type": "HIP",
      "active": true
    },
    {
      "id": "APOLLO01",
      "name": "Apollo Clinic",
      "facilityId": "IN3410000260",
      "type": "HIP",
      "active": true
    }
  ]
}
```

**Error responses:**

- `404` — Bridge ID not found in ABDM
- `502` — ABDM returned an unexpected error

**Query parameter:**

| Param       | Description                                    | Example        |
| ----------- | ---------------------------------------------- | -------------- |
| `bridge_id` | Your ABDM bridge ID (= `bridge_id` from step 2) | `SBXID_023051` |

---

### 5. GET `/abha/bridge/hospitals` — Kokoro DB snapshot

List all hospitals registered in Kokoro's DynamoDB. **Reads from DB — not a live ABDM call.**

Use this to retrieve `hospital_id` values before calling Phase 2 endpoints, or to cross-check the DB state against the live ABDM data from steps 3 and 4.

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
      "bridge_id": "SBXID_023051",
      "hip_id": "CITYHOSPITAL01",
      "hip_name": "CITYHOSPITAL01",
      "abdm_status": "registered",
      "created_at": "2025-05-27T10:15:00.000Z",
      "updated_at": "2025-05-27T10:15:00.000Z"
    }
  ],
  "count": 1
}
```

**Error responses:**

- `500` — Database error

**Useful for:** Confirming `hospital_id` before calling Phase 2 endpoints, debugging `X-HIP-ID` issues. To verify what ABDM actually has, use steps 3 or 4 instead.

---

## Phase 1 — ABHA User Onboarding

Two options depending on whether the user is new to ABHA or already has one.

---

### Option A — New ABHA Creation (via Aadhaar OTP)

---

#### 4. POST `/abha/create/request-otp`

Request OTP to the mobile linked to the user's Aadhaar. The OTP is sent by ABDM.

**Auth required:** None

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
- **Save `txn_id`** — required in the next step
- OTP is sent to the mobile registered with Aadhaar/ABDM

---

#### 5. POST `/abha/create/verify-otp`

Verify the OTP and create or retrieve the ABHA account. Saves profile + tokens to **AbhaAccounts**.

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

- `txn_id` must be from the previous step
- **Save `abha_number` from the response** — needed for profile and card
- **Keep `txn_id`** too — if the `mobile` you sent is **not** the Aadhaar-linked number, `abha_profile.mobile` comes back `null` (unverified). Run steps 5b–5c to link it, otherwise mobile login (Option C) will fail with `ABDM-1115`.

---

#### 5b. POST `/abha/create/mobile/request-otp` _(only if mobile ≠ Aadhaar mobile)_

ABHA Mobile Verification (Milestone 1 §3.0 Step 4a). Sends an OTP to the mobile number so it can be linked to the freshly created ABHA. Uses the **same `txn_id`** from step 5.

**Auth required:** None

**Postman setup:**

- **Method:** POST
- **URL:** `{{base_url}}/abha/create/mobile/request-otp`
- **Headers:** None required
- **Body (raw JSON):**

```json
{
  "txn_id": "abc123-txn-id-from-abdm",
  "mobile": "9587733170"
}
```

**Success response (200):**

```json
{
  "txn_id": "abc123-txn-id-from-abdm",
  "message": "OTP sent to mobile number ending with ******3372"
}
```

**Error responses:**

- `400` — Invalid mobile or txn_id not found / enrollment expired
- `500` — ABDM service error

**Important notes:**

- `txn_id` is the **enrollment** txn_id from step 5 (do not start a new transaction)

---

#### 5c. POST `/abha/create/mobile/verify-otp` _(only if mobile ≠ Aadhaar mobile)_

ABHA Mobile Verification (§3.0 Step 4b). Verifies the OTP and links the mobile to the ABHA. Note: this hits ABDM's `/enrollment/auth/byAbdm` endpoint and returns no tokens/profile.

**Auth required:** None

**Postman setup:**

- **Method:** POST
- **URL:** `{{base_url}}/abha/create/mobile/verify-otp`
- **Headers:** None required
- **Body (raw JSON):**

```json
{
  "txn_id": "abc123-txn-id-from-abdm",
  "otp": "123456"
}
```

**Success response (200):**

```json
{
  "message": "OTP verified successfully",
  "txn_id": "366c8f41-4ef8-49ee-b73a-2e3e44613086",
  "auth_result": "success"
}
```

**Error responses:**

- `400` — Invalid OTP or txn_id not found / OTP expired
- `500` — ABDM service error

**Important notes:**

- After success, the mobile is linked — call **step 6** (`GET /abha/profile`) to see the populated `mobile`, and mobile login (Option C) will now work for this number

---

#### 6. GET `/abha/profile` _(optional)_

Fetch the live ABHA profile from ABDM using the `abha_number` from step 5.

**Auth required:** None

**Postman setup:**

- **Method:** GET
- **URL:** `{{base_url}}/abha/profile?abha_number={{abha_number}}`
- **Headers:** None required
- **Body:** (none)

**Example:**

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
- `404` — No record found for this `abha_number` (run create or login first)
- `401` — Stored ABDM tokens expired (re-run the OTP flow)
- `500` — ABDM service error

**Important notes:**

- Returns **live data from ABDM**, not cached
- All profile fields are read-only from ABDM

---

#### 7. GET `/abha/card` _(optional)_

Download the official ABHA card as a Base64-encoded PDF.

**Auth required:** None

**Postman setup:**

- **Method:** GET
- **URL:** `{{base_url}}/abha/card?abha_number={{abha_number}}`
- **Headers:** None required
- **Body:** (none)

**Example:**

```
GET {{base_url}}/abha/card?abha_number=12-3456-7890-1234
```

**Success response (200):**

```json
{
  "card_base64": "JVBERi0xLjQKJeLjz9MNCjEgMCBvYmogICUgRW50cnkgcG9pbnQ..."
}
```

**Error responses:**

- `400` — `abha_number` query param missing
- `404` — No record found for this `abha_number`
- `401` — Stored ABDM tokens expired
- `500` — ABDM service error

**Important notes:**

- `card_base64` is a complete PDF file encoded as Base64
- **To decode in terminal:** `echo 'JVBERi...' | base64 -d > card.pdf && open card.pdf`
- The PDF contains the official ABHA card with QR code

---

### Option B — Login with Existing ABHA

---

#### 4. POST `/abha/login/request-otp`

Request OTP for an existing ABHA number. OTP is sent to the mobile registered with that ABHA.

**Auth required:** None

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
- **Save `txn_id`** — required in the next step

---

#### 5. POST `/abha/login/verify-otp`

Verify OTP and log in to ABHA. Saves fresh profile + tokens to **AbhaAccounts**.

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

- `txn_id` must be from the previous step
- **Save `abha_number` from the response** — needed for steps 6 and 7
- Token expiry: 30 min (access), 15 days (refresh)

---

#### 6. GET `/abha/profile` _(optional)_

Same as Option A step 6. Use `abha_number` from the login response above.

```
GET {{base_url}}/abha/profile?abha_number=12-3456-7890-1234
```

See [Option A step 6](#6-get-abhaprofile-optional) for full request/response details.

---

#### 7. GET `/abha/card` _(optional)_

Same as Option A step 7. Use `abha_number` from the login response above.

```
GET {{base_url}}/abha/card?abha_number=12-3456-7890-1234
```

See [Option A step 7](#7-get-abhacard-optional) for full request/response details.

---

### Option C — Login with Mobile Number

Login using only the registered **mobile number** — the user does not need to know their 14-digit ABHA number. Because a single mobile can be linked to **multiple** ABHA accounts, this is a **3-step** flow: step 2 returns the list of linked accounts plus a short-lived `t_token`, and step 3 selects one account to complete login.

---

#### 4a. POST `/abha/login/mobile/request-otp`

Request OTP for a mobile number. OTP is sent to that mobile via the ABDM OTP system.

**Auth required:** None

**Postman setup:**

- **Method:** POST
- **URL:** `{{base_url}}/abha/login/mobile/request-otp`
- **Headers:** None required
- **Body (raw JSON):**

```json
{
  "mobile": "9587733170"
}
```

**Success response (200):**

```json
{
  "txn_id": "ghi789-txn-id-from-abdm",
  "message": "OTP sent to mobile number ending with ******3170"
}
```

**Error responses:**

- `400` — Invalid mobile number
- `404` — No ABHA account linked to this mobile
- `500` — ABDM service error

**Important notes:**

- **Save `txn_id`** — required in the next step

---

#### 5a. POST `/abha/login/mobile/verify-otp`

Verify the OTP. Returns a **short-lived (5 min) `t_token`** and the list of ABHA accounts linked to the mobile. **No session is created yet** — pick one account and continue to step 6a.

**Auth required:** None

**Postman setup:**

- **Method:** POST
- **URL:** `{{base_url}}/abha/login/mobile/verify-otp`
- **Headers:** None required
- **Body (raw JSON):**

```json
{
  "txn_id": "ghi789-txn-id-from-abdm",
  "otp": "123456"
}
```

**Success response (200):**

```json
{
  "message": "OTP verified successfully",
  "txn_id": "ghi789-txn-id-from-abdm",
  "t_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "expires_in": 300,
  "accounts": [
    {
      "ABHANumber": "91-2568-7073-XXXX",
      "preferredAbhaAddress": "johndoe@sbx",
      "name": "John Doe",
      "gender": "M",
      "dob": "07-03-1997",
      "status": "ACTIVE",
      "kycVerified": true
    }
  ]
}
```

**Error responses:**

- `400` — Invalid OTP or txn_id not found
- `500` — ABDM service error

**Important notes:**

- `t_token` is valid for **5 minutes** — complete step 6a before it expires
- **Save `txn_id`, `t_token`, and the chosen `ABHANumber`** — all three are needed in the next step

---

#### 6a. POST `/abha/login/mobile/verify-user`

Select one ABHA account from step 5a and obtain the final session token. Saves fresh profile + tokens to **AbhaAccounts**.

**Auth required:** None (the short-lived `t_token` is passed in the body, not as an auth header)

**Postman setup:**

- **Method:** POST
- **URL:** `{{base_url}}/abha/login/mobile/verify-user`
- **Headers:** None required
- **Body (raw JSON):**

```json
{
  "txn_id": "ghi789-txn-id-from-abdm",
  "abha_number": "91-2568-7073-XXXX",
  "t_token": "eyJ0eXAiOiJKV1QiLCJhbGc..."
}
```

**Success response (200):**

```json
{
  "message": "Login verified",
  "abha_number": "91-2568-7073-XXXX",
  "abha_profile": {
    "ABHANumber": "91-2568-7073-XXXX",
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

- `400` — `abha_number` not in the step 5a list, or `t_token` expired/invalid
- `500` — Database or ABDM service error

**Important notes:**

- `abha_number` must be one of the accounts returned in step 5a
- **Save `abha_number` from the response** — needed for profile and card (Option A steps 6 & 7)
- Token expiry: 30 min (access), 15 days (refresh)

---

## Phase 1D — Provision Kokoro User from ABHA

After the patient has created or logged into their ABHA (Phase 1 Options A–C), provision a **loginable** Kokoro user from the ABHA record.

> **This endpoint lives in auth_lambda, not abha_lambda.** It was moved there so it can create both the `Users` record and the `AuthTable` record (and issue a JWT) using the canonical auth path — meaning the user can log in immediately. See **`POST /auth/abha/signup-user`** in [auth-lambda.md](auth-lambda.md).

Quick summary: send `{ abha_number, hospital_id }` to `POST /auth/abha/signup-user`. It sources phone/name/email from the `AbhaAccounts` row, creates+links the user, writes `kokoro_user_id` back onto the ABHA row, and returns a JWT. Login afterward is `POST /auth/login` with the mobile (passwordless).

---

## Phase 2 — HIP-Initiated Linking (Milestone 2)

Hospital initiates record linking with ABDM. All linking calls are **asynchronous** — Kokoro returns `request_id` immediately and ABDM POSTs the result back to the registered webhook. Requires Phase 0 setup (hospital registered, bridge URL set).

---

### 8. POST `/abha/link/generate-token`

Ask ABDM to generate a link token for a patient at a specific hospital. ABDM calls back to `/api/v3/hip/token/on-generate-token` with the token.

**Auth required:** None

**Postman setup:**

- **Method:** POST
- **URL:** `{{base_url}}/abha/link/generate-token`
- **Headers:**

```
Content-Type: application/json
```

- **Body using ABHA address (preferred):**

```json
{
  "hospital_id": "hosp-uuid-123",
  "abha_address": "john.doe@abdm",
  "name": "John Doe",
  "gender": "M",
  "year_of_birth": 1990
}
```

- **Body using ABHA number instead:**

```json
{
  "hospital_id": "hosp-uuid-123",
  "abha_number": "12-3456-7890-1234",
  "name": "John Doe",
  "gender": "M",
  "year_of_birth": 1990
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
- `404` — `hospital_id` not found (run Phase 0 setup first)
- `403` — Hospital ABDM status is `inactive`
- `500` — ABDM service error

**Important notes:**

- Provide `abha_address` **or** `abha_number`, not both
- Gender values: `M`, `F`, `O`
- **Save `request_id`** — use it in step 10 to check status
- Link token is hospital-scoped — use same `hospital_id` in step 11

---

### 9. POST `/api/v3/hip/token/on-generate-token` ← ABDM → Kokoro callback

ABDM POSTs the link token here after processing step 8. **You do not call this — ABDM does.**

**Triggered by:** ABDM after a successful `generate-token` request

**What ABDM sends:**

```
POST {bridge_url}/api/v3/hip/token/on-generate-token
X-HIP-ID: CITYHOSPITAL01
REQUEST-ID: <new-uuid>
TIMESTAMP: 2025-05-27T10:16:30Z

{
  "abhaAddress": "john.doe@abdm",
  "linkToken": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "response": {
    "requestId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
  }
}
```

**What Kokoro does automatically:**

1. Validates `X-HIP-ID` header
2. Stores `linkToken` in **AbhaAccounts** keyed by `abhaAddress` + `hip_id`
3. Updates **AbdmTransactions** row to `COMPLETED` (matched by `response.requestId`)
4. Returns `{}` HTTP `202`

**To verify it arrived:** Use step 10 below.

---

### 10. GET `/abha/transactions` — Check token generation status

Poll this after step 8 to confirm ABDM sent the link token callback.

**Auth required:** None

**Postman setup:**

- **Method:** GET
- **URL:** `{{base_url}}/abha/transactions?request_id={{request_id}}`
- **Headers:** None required

**Success response — COMPLETED (token received):**

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

**Success response — still PENDING:**

```json
{
  "transaction": {
    "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "api": "generate-token",
    "status": "PENDING",
    "created_at": "2025-05-27T10:15:00.000Z"
  }
}
```

**Error responses:**

- `404` — `request_id` not found

**Other query params (for bulk monitoring):**

| Param    | Description                                    | Example          |
| -------- | ---------------------------------------------- | ---------------- |
| `hip_id` | All transactions for one hospital              | `CITYHOSPITAL01` |
| `status` | Filter by `PENDING` \| `COMPLETED` \| `FAILED` | `PENDING`        |
| `limit`  | Max rows (default 50)                          | `20`             |

**When to proceed to step 11:** Only when `status` = `COMPLETED`. If stuck in `PENDING`, verify your bridge URL is correctly registered (step 1) and reachable from ABDM.

---

### 11. POST `/abha/link/care-context`

Link care contexts (medical records) to the patient's ABHA. Uses the link token stored automatically in step 9. Must use the same `hospital_id` as step 8.

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

- `409` — No valid link token for this patient at this hospital (step 8 not done or token expired)
- `404` — Hospital not registered or `abha_number` cannot be resolved from `abha_address`
- `403` — Hospital ABDM registration is `inactive`
- `400` — Invalid patient/care context structure
- `500` — ABDM service error

**Important notes:**

- **Must use the same `hospital_id` as step 8**
- `hiType` values: `PRESCRIPTION`, `DiagnosticReport`, `OPConsultation`, `LabReport`, `DischargeSummary`
- `count` = total number of care contexts for that `hiType`
- Can include multiple care contexts per patient in one call
- **Save `request_id`** — use it in step 13 to check status

---

### 12. POST `/api/v3/link/on_carecontext` ← ABDM → Kokoro callback

ABDM POSTs the linking result here after processing step 11. **You do not call this — ABDM does.**

**Triggered by:** ABDM after processing a `care-context` request

**What ABDM sends (success):**

```
POST {bridge_url}/api/v3/link/on_carecontext
X-HIP-ID: CITYHOSPITAL01
REQUEST-ID: <new-uuid>

{
  "abhaAddress": "john.doe@abdm",
  "status": "Successfully Linked care context",
  "response": {
    "requestId": "b2c3d4e5-f6a7-8901-bcde-f12345678901"
  }
}
```

**What ABDM sends (error):**

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

**What Kokoro does automatically:**

1. Validates `X-HIP-ID` header (hospital routing)
2. If `status` present → marks **AbdmTransactions** row `COMPLETED`
3. If `error` present → marks row `FAILED`, stores error details
4. Returns `{}` HTTP `202`

**Common ABDM error codes:**

| Code        | Meaning                     | Action                              |
| ----------- | --------------------------- | ----------------------------------- |
| `ABDM-1056` | Care context already linked | Check if already linked in ABDM     |
| `ABDM-1050` | Patient not found           | Verify ABHA address/number          |
| `ABDM-1055` | Invalid care context format | Check patient/careContext structure |

**To verify result:** Use step 13 below.

---

### 13. GET `/abha/transactions` — Check linking status

Poll this after step 11 to confirm ABDM confirmed or rejected the care context linking.

**Auth required:** None

**Postman setup:**

- **Method:** GET
- **URL:** `{{base_url}}/abha/transactions?request_id={{request_id}}`
- **Headers:** None required

**Response — COMPLETED (linking succeeded):**

```json
{
  "transaction": {
    "request_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
    "api": "link-carecontext",
    "hospital_id": "hosp-uuid-123",
    "hip_id": "CITYHOSPITAL01",
    "abha_address": "john.doe@abdm",
    "status": "COMPLETED",
    "callback_payload": {
      "abhaAddress": "john.doe@abdm",
      "status": "Successfully Linked care context",
      "response": {
        "requestId": "b2c3d4e5-f6a7-8901-bcde-f12345678901"
      }
    },
    "callback_received_at": "2025-05-27T10:21:00.000Z",
    "created_at": "2025-05-27T10:20:00.000Z"
  }
}
```

**Response — FAILED (ABDM rejected linking):**

```json
{
  "transaction": {
    "request_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
    "api": "link-carecontext",
    "status": "FAILED",
    "callback_payload": {
      "abhaAddress": "john.doe@abdm",
      "error": {
        "code": "ABDM-1056",
        "message": "This care context has been already linked"
      },
      "response": {
        "requestId": "b2c3d4e5-f6a7-8901-bcde-f12345678901"
      }
    },
    "callback_received_at": "2025-05-27T10:21:00.000Z"
  }
}
```

**Debugging tips:**

- `PENDING` after 30+ seconds → webhook not arriving, check bridge URL registration
- `FAILED` → read `callback_payload.error.code` against the ABDM error table above
- For bulk monitoring: `GET /abha/transactions?hip_id=CITYHOSPITAL01&status=FAILED&limit=20`

---

## Phase 3 — HIP Data Flow (Consent → Data Transfer)

Triggered after Phase 2 linking is complete. The patient approves a consent request (via ABDM/HIU), ABDM notifies Kokoro, and Kokoro pushes the health records to the HIU.

**How it works:**
- Steps 14 and 16 are **inbound callbacks from ABDM** — you don't call them, ABDM does.
- After each inbound callback, Kokoro **automatically** fires the outbound calls (steps 15, 17–19).
- As a developer you only need to **monitor** the flow using `GET /abha/transactions`.

**Data stored:** The full consent artefact from step 14 is persisted in the **ConsentArtefacts** DynamoDB table keyed by `consentId`. This is needed because step 16 only sends the `consentId` — without the stored artefact Kokoro wouldn't know which care context references to push.

**Encryption note:** The FHIR bundle encryption (step 18) uses ECDH Curve25519 + AES-GCM with the HIU's public key from step 16. As of the Milestone 3 work the encryption module (`app/abdm/data_encryption.py`) is **fully implemented** (X25519 ECDH → HKDF-SHA256 → AES-256-GCM, SHA-256 checksum) and is shared with the HIU receive path. The remaining placeholder is `_build_fhir_bundle()` in `data_flow_service.py`, which still emits a structurally-empty bundle — the push now completes cryptographically but the clinical FHIR content is not yet assembled from Kokoro's records.

---

### 14. POST `/api/v3/consent/request/hip/notify` ← ABDM → Kokoro callback

ABDM POSTs the full consent artefact here when a patient approves (or revokes) a consent request. **You do not call this — ABDM does.**

**Triggered by:** Patient approving a consent request in the ABDM app/PHR app.

**What ABDM sends:**
```
POST {bridge_url}/api/v3/consent/request/hip/notify
X-HIP-ID: CITYHOSPITAL01
REQUEST-ID: <new-uuid>
TIMESTAMP: 2025-05-27T10:00:00.000Z

{
  "notification": {
    "status": "GRANTED",
    "consentId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "consentDetail": {
      "schemaVersion": "v3",
      "consentId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "createdAt": "2024-05-01T05:10:20.123Z",
      "patient": { "id": "john.doe@abdm" },
      "careContexts": [
        {
          "patientReference": "PAT-001",
          "careContextReference": "CC-2025-001"
        }
      ],
      "purpose": { "text": "Care Management", "code": "CAREMGT", "refUri": "www.abc.com" },
      "hip": { "id": "CITYHOSPITAL01", "name": "City Hospital", "type": "HIP" },
      "hiu": { "id": "HIU-001", "name": "Requesting App", "type": "HIU" },
      "hiTypes": ["OPConsultation", "DiagnosticReport"],
      "permission": {
        "accessMode": "VIEW",
        "dateRange": {
          "from": "2024-01-01T00:00:00.000Z",
          "to": "2025-01-01T00:00:00.000Z"
        },
        "dataEraseAt": "2026-01-01T00:00:00.000Z",
        "frequency": { "unit": "HOUR", "value": 1, "repeats": 0 }
      }
    },
    "signature": "e8nY601CYDs...",
    "grantAcknowledgement": false
  }
}
```

**What Kokoro does automatically:**
1. Persists the full consent artefact to **ConsentArtefacts** table (keyed by `consentId`)
2. Fires the **step 15** acknowledgement to ABDM (6.3.2)
3. Returns `{}` HTTP `202` to ABDM

**Key values to note in the body:**
| Field | Where used |
|-------|-----------|
| `notification.consentId` | Key for **ConsentArtefacts** and later data push |
| `notification.consentDetail.careContexts[].careContextReference` | Used in step 18 to know which records to push |
| `notification.status` | `GRANTED` starts the flow; `REVOKED` stops future pushes |
| `REQUEST-ID` header | Echoed back in step 15 as `response.requestId` |

---

### 15. Automatic → POST `/api/hiecm/consent/v3/request/hip/on-notify` (Kokoro → ABDM, 6.3.2)

Kokoro fires this automatically after step 14, acknowledging receipt of the consent notification. **Not callable from Postman.**

**Body Kokoro sends to ABDM:**
```json
{
  "acknowledgement": {
    "status": "OK",
    "consentId": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
  },
  "response": {
    "requestId": "<REQUEST-ID from the step 14 callback header>"
  }
}
```

**ABDM responds:** `202 Accepted`

---

### 16. POST `/api/v3/hip/health-information/request` ← ABDM → Kokoro callback

ABDM forwards the HIU's data request here: the `consentId`, `dataPushUrl` (where to push the records), and the HIU's ECDH public key for encryption. **You do not call this — ABDM does.**

**Triggered by:** HIU requesting the health data after consent is granted.

**What ABDM sends:**
```
POST {bridge_url}/api/v3/hip/health-information/request
X-HIP-ID: CITYHOSPITAL01
REQUEST-ID: <transaction-uuid>
TIMESTAMP: 2025-05-27T10:05:00.000Z

{
  "transactionId": "18235d89-cb13-479d-ad71-7a57d5f669a8",
  "hiRequest": {
    "consent": {
      "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    },
    "dateRange": {
      "from": "2024-01-01T00:00:00.000Z",
      "to": "2025-01-01T00:00:00.000Z"
    },
    "dataPushUrl": "https://hiu-server.example.com/v3/data/push",
    "keyMaterial": {
      "cryptoAlg": "ECDH",
      "curve": "Curve25519",
      "dhPublicKey": {
        "expiry": "2124-12-09T00:00:00.000Z",
        "parameters": "Curve25519/32byte random key",
        "keyValue": "BCpsBW37KgfLyjxJK0zHHG26hDjxzK368DEO4Pap..."
      },
      "nonce": "0ka0stPfqmXWhX+ODC/iOFMO0PXFdRjBdcEGbv55qqc="
    }
  }
}
```

**What Kokoro does automatically (the full 6.3.3 → 6.3.6 orchestration):**
1. Records a `PENDING` row in **AbdmTransactions** keyed by `transactionId`
2. Fires **step 17** acknowledgement immediately
3. Looks up the consent artefact from **ConsentArtefacts** (saved in step 14)
4. Builds FHIR bundles per care context reference
5. Encrypts them using the HIU's `keyMaterial` (ECDH + AES-GCM) → **step 18**
6. Notifies the CM of transfer success/failure → **step 19**

**Key values in the body:**
| Field | Purpose |
|-------|---------|
| `transactionId` | Tracks the full data-push flow in **AbdmTransactions** |
| `hiRequest.consent.id` | Looks up the stored consent artefact from step 14 |
| `hiRequest.dataPushUrl` | Where Kokoro pushes encrypted records (step 18) |
| `hiRequest.keyMaterial` | HIU's ECDH public key — used to encrypt records |

**To monitor this flow:**
```
GET {{base_url}}/abha/transactions?request_id={{transaction_id}}
```
- `PENDING` = acknowledgement sent, waiting for push+notify to complete
- `COMPLETED` = data pushed and CM notified
- `FAILED` = push or notify failed (see `callback_payload.error`)

---

### 17. Automatic → POST `/api/hiecm/data-flow/v3/health-information/hip/on-request` (Kokoro → ABDM, 6.3.4)

Kokoro fires this immediately after step 16, acknowledging the HI request. **Not callable from Postman.**

**Body Kokoro sends to ABDM:**
```json
{
  "hiRequest": {
    "transactionId": "18235d89-cb13-479d-ad71-7a57d5f669a8",
    "sessionStatus": "ACKNOWLEDGED"
  },
  "response": {
    "requestId": "<REQUEST-ID from the step 16 callback header>"
  }
}
```

**ABDM responds:** `202 Accepted`

---

### 18. Automatic → POST `{dataPushUrl}` (Kokoro → HIU, 6.3.5)

Kokoro pushes the encrypted FHIR records directly to the HIU's `dataPushUrl` from step 16. **Not callable from Postman — target URL is HIU-controlled.**

**Encryption:** ECDH Curve25519 key exchange against the HIU's public key from step 16. Each FHIR bundle is AES-GCM encrypted. The HIP's own ephemeral public key + nonce (`keyMaterial`) is sent alongside so the HIU can derive the same shared secret and decrypt.

> **Current status:** The encryption module (`app/abdm/data_encryption.py`) is now implemented (real X25519 ECDH + AES-256-GCM). The push therefore completes and the `AbdmTransactions` row reaches `COMPLETED`. **Caveat:** until `_build_fhir_bundle()` is replaced with real clinical-record assembly, the encrypted payload contains a placeholder (empty) FHIR bundle — wire it to Kokoro's clinical store before certification.

**Body Kokoro sends to HIU:**
```json
{
  "pageNumber": 1,
  "pageCount": 1,
  "transactionId": "18235d89-cb13-479d-ad71-7a57d5f669a8",
  "entries": [
    {
      "content": "<base64 AES-GCM encrypted FHIR bundle>",
      "media": "application/fhir+json",
      "checksum": "<sha-256 of plaintext bundle>",
      "careContextReference": "CC-2025-001"
    }
  ],
  "keyMaterial": {
    "cryptoAlg": "ECDH",
    "curve": "Curve25519",
    "dhPublicKey": {
      "expiry": "2026-01-01T00:00:00.000Z",
      "parameters": "Curve25519/32byte random key",
      "keyValue": "<base64 HIP ephemeral public key>"
    },
    "nonce": "<base64 HIP nonce>"
  }
}
```

**HIU responds:** `202 Accepted`

---

### 19. Automatic → POST `/api/hiecm/data-flow/v3/health-information/notify` (Kokoro → ABDM, 6.3.6)

Kokoro tells the CM whether the data transfer succeeded. Fired after step 18 completes. **Not callable from Postman.**

**Body on success:**
```json
{
  "notification": {
    "consentId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "transactionId": "18235d89-cb13-479d-ad71-7a57d5f669a8",
    "doneAt": "2025-05-27T10:06:30.000Z",
    "notifier": {
      "type": "HIP",
      "id": "CITYHOSPITAL01"
    },
    "statusNotification": {
      "sessionStatus": "TRANSFERRED",
      "hipId": "CITYHOSPITAL01",
      "statusResponses": [
        {
          "careContextReference": "CC-2025-001",
          "hiStatus": "OK",
          "description": ""
        }
      ]
    }
  }
}
```

**Body on failure:**
```json
{
  "notification": {
    "consentId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "transactionId": "18235d89-cb13-479d-ad71-7a57d5f669a8",
    "doneAt": "2025-05-27T10:06:30.000Z",
    "notifier": { "type": "HIP", "id": "CITYHOSPITAL01" },
    "statusNotification": {
      "sessionStatus": "FAILED",
      "hipId": "CITYHOSPITAL01",
      "statusResponses": [
        {
          "careContextReference": "CC-2025-001",
          "hiStatus": "ERRORED",
          "description": "<error message, truncated to 200 chars>"
        }
      ]
    }
  }
}
```

**ABDM responds:** `202 Accepted`

**After this call completes:**
- `AbdmTransactions` row → `COMPLETED` (success) or `FAILED`
- Poll with `GET {{base_url}}/abha/transactions?request_id={{transaction_id}}` to confirm

---

### Monitoring Phase 3

Check status of a specific data flow transaction:
```
GET {{base_url}}/abha/transactions?request_id={{transaction_id}}
```

Check all pending data-flow requests for a hospital:
```
GET {{base_url}}/abha/transactions?hip_id=CITYHOSPITAL01&status=PENDING&limit=20
```

**`api` field values in AbdmTransactions for Phase 3:** `hi-data-flow`

**Phase 3 transaction lifecycle:**
```
PENDING  → acknowledgement sent (step 17), push not yet done
COMPLETED → pushed (step 18) + CM notified (step 19) successfully
FAILED   → push or notify errored; check callback_payload.error
```

**Stuck at PENDING after encryption is implemented?** Check:
1. `hiRequest.dataPushUrl` was reachable from Kokoro's network
2. HIU's `keyMaterial.keyValue` is a valid Curve25519 base64 public key
3. The consent artefact was persisted in step 14 (query `ConsentArtefacts` table directly)

---

## Phase 4 — HIU Flow (Milestone 3)

Kokoro acts as **HIU** on a hospital's behalf: it asks a patient for consent, then pulls that patient's records from **another** HIP. Every call is asynchronous — Kokoro returns a `request_id` and ABDM POSTs results to the registered webhook (`/api/v3/hiu/...`).

**Prerequisites:**
- Phase 0 done (hospital registered via `register-facility`; bridge URL set). Registration now stores `hiu_id` (= `hip_id` in sandbox) so the hospital can act as HIU.
- `KOKORO_WEBHOOK_BASE_URL` env var **must be set** to Kokoro's public base URL — it is used to build the `dataPushUrl` ABDM hands to the source HIP. Without it, `health-information/request` returns `400`.

**State tables:** `HiuConsentRequests` (consent lifecycle, keyed by `request_id`, GSI on `consent_request_id`) and `HiuDataRequests` (data request + the ephemeral X25519 private key used to decrypt the inbound push, keyed by `request_id`, GSI on `transaction_id`). Fetched artefacts are stored in the shared `ConsentArtefacts` table.

**How it works:** You call the four outbound endpoints below; ABDM drives the five inbound callbacks automatically (Kokoro stores state, acknowledges, decrypts, and notifies the CM). Monitor with the `GET` inspection endpoints.

---

### 20. POST `/abha/hiu/consent/request` — 4.3.1 raise a consent request

**Auth required:** None

- **Method:** POST · **URL:** `{{base_url}}/abha/hiu/consent/request`
- **Body (raw JSON):**

```json
{
  "hospital_id": "hosp-uuid-123",
  "patient_abha_address": "john.doe@sbx",
  "hi_types": ["OPConsultation", "DiagnosticReport"],
  "date_from": "2024-01-01T00:00:00.000Z",
  "date_to": "2025-01-01T00:00:00.000Z",
  "data_erase_at": "2026-01-01T00:00:00.000Z",
  "requester_name": "Dr. Manju",
  "requester_id_value": "MH1001",
  "hip_id": "OTHERHOSPITAL01"
}
```

**Success (200):**

```json
{ "message": "Consent request accepted. consentRequestId will arrive via on-init.",
  "request_id": "a1b2...", "hospital_id": "hosp-uuid-123" }
```

Optional fields default sensibly: `requester_id_type` (`REGNO`), `requester_id_system` (MCI), `purpose_code` (`CAREMGT`), `access_mode` (`VIEW`), `frequency_*`. `hip_id` and `care_contexts` are optional (omit to let the patient pick).

**Save `request_id`** — use it in step 22 to read the assigned `consentRequestId`.

---

### 21. POST `/api/v3/hiu/consent/request/on-init` ← ABDM → Kokoro callback (4.3.2)

ABDM returns the assigned `consentRequest.id`, correlated by `response.requestId` (the `request_id` from step 20). Kokoro stores it on the `HiuConsentRequests` row. **You don't call this.**

---

### 22. GET `/abha/hiu/consent/request/{request_id}` — inspect consent state

Returns the row: `status` (`REQUESTED` → `GRANTED`/`DENIED`/`REVOKED`), `consent_request_id` (after step 21), `consent_ids` (after the patient approves, step 23).

---

### 23. POST `/api/v3/hiu/consent/request/notify` ← ABDM → Kokoro callback

Patient approved / denied / revoked. ABDM sends `notification.consentRequestId`, `status`, and `consentArtefacts: [{ id }]`. Kokoro stores the granted `consentId`(s), then **automatically acknowledges** via 4.3.4 (`/api/hiecm/consent/v3/request/hiu/on-notify`), echoing the callback's REQUEST-ID. **You don't call this.**

---

### 24. POST `/abha/hiu/consent/status` — 4.3.5 poll status _(optional)_

Body: `{ "hospital_id": "...", "consent_request_id": "..." }`. Result arrives at `/api/v3/hiu/consent/request/on-status` (4.3.6) and updates the row.

---

### 25. POST `/abha/hiu/consent/fetch` — 4.3.7 fetch the artefact

Body: `{ "hospital_id": "...", "consent_id": "..." }`. The full artefact + signature arrive at `/api/v3/hiu/consent/on-fetch` (4.3.8) and are stored in `ConsentArtefacts` keyed by `consentId`.

---

### 26. POST `/abha/hiu/health-information/request` — request the records

**Auth required:** None

- **Body (raw JSON):**

```json
{
  "hospital_id": "hosp-uuid-123",
  "consent_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "date_from": "2024-01-01T00:00:00.000Z",
  "date_to": "2025-01-01T00:00:00.000Z"
}
```

**What Kokoro does:** generates an ephemeral X25519 key pair, persists the **private** key in `HiuDataRequests` keyed by `request_id`, sends ABDM our **public** key + the `dataPushUrl` (`{KOKORO_WEBHOOK_BASE_URL}/api/v3/hiu/health-information/transfer`). Returns `request_id`.

---

### 27. POST `/api/v3/hiu/health-information/transfer` ← HIP → Kokoro data push (6.3.5 inbound)

The source HIP pushes encrypted FHIR `entries` + its own `keyMaterial` to our `dataPushUrl`. Kokoro **automatically**:
1. Matches the push to our request by `transactionId` (falls back to the latest `PENDING`).
2. Decrypts each entry with the stored private key (ECDH → AES-GCM), verifies the SHA-256 checksum.
3. Persists the decrypted FHIR on the `HiuDataRequests` row (`status → RECEIVED`).
4. Notifies the CM (6.3.6) with `notifier.type = HIU`, `sessionStatus = RECEIVED`.

> **Production note:** decrypted bundles are currently stored on the data-request row for inspection — wire them into the patient's clinical record store. The ephemeral private key is persisted to enable async decryption; wrap it with KMS before production (WASA audit H-6).

**You don't call this** — the HIP does.

---

### 28. GET `/abha/hiu/data/{request_id}` — inspect received data

Returns the `HiuDataRequests` row with decrypted `received_bundles` once `status = RECEIVED`. The ephemeral private key and nonce are **redacted** from this response.

---

### HIU callback paths (all auto-routed to ABHALambda via `/api/v3/{proxy+}`)

| Path | Spec | Purpose |
|------|------|---------|
| `/api/v3/hiu/consent/request/on-init` | 4.3.2 | consentRequestId assigned |
| `/api/v3/hiu/consent/request/notify` | — | patient approved/denied/revoked (auto-acked 4.3.4) |
| `/api/v3/hiu/consent/request/on-status` | 4.3.6 | status poll result |
| `/api/v3/hiu/consent/on-fetch` | 4.3.8 | artefact delivery |
| `/api/v3/hiu/health-information/transfer` | 6.3.5 | encrypted record push (decrypt + notify) |

> **Security caveat (carried from the architecture review):** these HIU callbacks, like the M2 callbacks, do **not yet validate the ABDM gateway JWT**, and the data-push URL is the bridge URL set by an unauthenticated admin endpoint. Add callback JWT validation + consent-signature verification before production onboarding.

---

## Postman Setup Guide

### Environment variables

Go to **Postman → Environments → Edit** and add:

| Variable          | Example Value               | Set when                                   |
| ----------------- | --------------------------- | ------------------------------------------ |
| `base_url`        | `http://localhost:8000`     | Always                                     |
| `aadhaar`         | `123456789012`              | Before Phase 1A                            |
| `mobile`          | `9587733170`                | Before Phase 1A                            |
| `abha_number`     | `12-3456-7890-1234`         | After step 5 (create / login) or 6a (mobile login) |
| `abha_address`    | `john.doe@abdm`             | After step 6 (profile)                     |
| `hospital_id`     | `hosp-uuid-123`             | After step 2 (register-facility)           |
| `hip_id`          | `CITYHOSPITAL01`            | After step 2 (register-facility)           |
| `bridge_id`       | `SBXID_023051`              | Before step 2 (your ABDM bridge ID)        |
| `txn_id`          | _(auto-set by test script)_ | Auto from step 4 / 4a                       |
| `t_token`         | _(auto-set by test script)_ | Auto from step 5a (mobile login, 5 min TTL) |
| `request_id`      | _(auto-set by test script)_ | Auto from steps 8 and 11                  |
| `transaction_id`  | _(sent by ABDM)_            | From Phase 3 step 16 callback body         |

**No auth headers needed anywhere.** All endpoints are open.

---

### Auto-extract IDs with test scripts

Add to the **Tests** tab of step 4 (request-otp):

```javascript
pm.environment.set("txn_id", pm.response.json().txn_id);
```

Add to the **Tests** tab of step 5 (verify-otp):

```javascript
pm.environment.set("abha_number", pm.response.json().abha_number);
```

Add to the **Tests** tab of steps 8 and 11 (generate-token / care-context):

```javascript
pm.environment.set("request_id", pm.response.json().request_id);
```

**Mobile login (Option C):** add to the **Tests** tab of step 5a (mobile verify-otp) to capture the short-lived token and pick the first linked account:

```javascript
const r = pm.response.json();
pm.environment.set("txn_id", r.txn_id);
pm.environment.set("t_token", r.t_token);
pm.environment.set("abha_number", r.accounts[0].ABHANumber); // or let the user choose
```

---

### Decode ABHA card PDF

After step 7 in terminal:

```bash
echo 'JVBERi0xLjQK...' | base64 -d > card.pdf
open card.pdf  # Mac
```

---

### Common errors

| Status                           | Meaning                                 | Fix                                                                            |
| -------------------------------- | --------------------------------------- | ------------------------------------------------------------------------------ |
| `400`                            | Missing or invalid field                | Check request body against docs above                                          |
| `401`                            | Stored ABDM tokens expired              | Re-run OTP flow (steps 4–5) to refresh tokens                                  |
| `404`                            | Record not found                        | Run setup steps first; confirm `abha_number` / `hospital_id` is correct        |
| `409`                            | No link token for this patient+hospital | Run step 8 and wait for step 9 callback before calling step 11                 |
| `403`                            | Hospital inactive                       | Set `active: true` via register-facility                                       |
| `500`                            | ABDM or DB error                        | Check backend logs; verify ABDM sandbox is reachable                           |
| `"Missing Authentication Token"` | API Gateway route not found             | Deploy latest `template.yaml` — PATCH/PUT/DELETE methods may not be registered |
