# Auth Lambda

### POST `/auth/user/request-signup-otp`

Request OTP for user signup. OTP is sent to email only.

```json
{
  "phoneNumber": "+919587733170",
  "email": "user@example.com"
}
```

**Note:** Phone number formats are normalized automatically:

- `+919587733170` (with country code)
- `9587733170` (10 digits, assumes +91)
- `919587733170` (12 digits, adds +)

---

### POST `/auth/doctor/request-signup-otp`

Request OTP for doctor signup. OTP is sent to email only.

```json
{
  "phoneNumber": "+919587733170",
  "email": "doctor@example.com"
}
```

---

### POST `/auth/request-otp`

Request OTP for login (existing users/doctors). Can be sent to email (default) or SMS (if phone verified).

```json
{
  "identifier": "+919587733170",
  "preferredChannel": "email"
}
```

**Alternative (SMS if phone verified):**

```json
{
  "identifier": "+919587733170",
  "preferredChannel": "sms"
}
```

**With email:**

```json
{
  "identifier": "user@example.com",
  "preferredChannel": "email"
}
```

**Note:** `identifier` can be phone number or email. `preferredChannel` defaults to `"email"` if not provided.

---

### POST `/auth/login`

Login with OTP (or get role discovery if OTP not provided).

**With OTP:**

```json
{
  "identifier": "+919587733170",
  "otp": "1234"
}
```

**With email:**

```json
{
  "identifier": "user@example.com",
  "otp": "1234"
}
```

**Without OTP (role discovery):**

```json
{
  "identifier": "+919587733170"
}
```

**Note:** `identifier` can be phone number or email. `otp` is optional - if not provided, returns role discovery information.

---

### POST `/auth/user/signup`

Complete user signup. Supports two flows:

**Normal flow (with email and OTP):**

```json
{
  "phoneNumber": "+919587733170",
  "email": "john.doe@example.com",
  "otp": "1234",
  "name": "John Doe"
}
```

**Experimental flow (mobile-only, no email/OTP):**

```json
{
  "phoneNumber": "+919587733170",
  "name": "John Doe"
}
```

**Minimal experimental flow:**

```json
{
  "phoneNumber": "+919587733170"
}
```

**Note:**

- Normal flow requires `email` and `otp`. Email is verified during signup.
- Experimental flow skips email/OTP verification. All fields except `phoneNumber` are optional.

---

### POST `/auth/abha/signup-user`

Provision a **loginable** Kokoro user from an existing ABHA account and link them together. Trigger this **after** the patient has created/logged into their ABHA (abha_lambda Phase 1) — the `AbhaAccounts` row must already exist.

Only `abha_number` and `hospital_id` are sent; identity (phone, name, email) is sourced from the stored ABHA record. The mobile on the ABHA record becomes the login credential — login is passwordless (`POST /auth/login` with the mobile, no OTP), so the user can sign in immediately.

Creates a `Users` record **and** an `AuthTable` record (so the user can log in), writes a `UserHospital` membership row (patient ↔ hospital M:N), stores `abha_number` on the user, writes `kokoro_user_id` back onto the `AbhaAccounts` row, and returns a JWT.

**Request body:**

```json
{
  "abha_number": "12-3456-7890-1234",
  "hospital_id": "hosp-uuid-123"
}
```

**Success response (200):**

```json
{
  "message": "User provisioned from ABHA successfully.",
  "access_token": "eyJ...",
  "user_id": "usr_a1b2c3d4-...",
  "abha_number": "12-3456-7890-1234",
  "hospital_id": "hosp-uuid-123",
  "created": true,
  "already_linked": false
}
```

**Response fields:**

| Field | Meaning |
|-------|---------|
| `access_token` | JWT for the provisioned user — they are logged in immediately |
| `user_id` | Kokoro user ID (`usr_<uuid>`) |
| `created` | `true` = new Users record written; `false` = reused existing user |
| `already_linked` | `true` = this ABHA was already linked to a user (idempotent return, still returns a fresh JWT) |

**Error responses:**

- `400` — ABHA record has no verified mobile (run abha_lambda Flow A2 to link a mobile first)
- `404` — No `AbhaAccounts` row for this `abha_number` (create the ABHA first)
- `500` — Database error

**Behaviour notes:**

- **Idempotent:** if the ABHA is already linked to a `user_id`, returns that user with a fresh JWT (`already_linked: true`).
- **Link-or-create:** if no user is linked yet but a Kokoro user already exists with the ABHA's mobile, that user is reused (no duplicate).
- **Multi-hospital:** a `UserHospital` membership row is written in **all** branches (new user, reused user, already-linked). The user is additively linked to the requesting hospital without removing prior hospital memberships.
- **Login afterward:** `POST /auth/login` with `{ "identifier": "<mobile>" }` (no OTP) returns a JWT — the mobile is the credential.

---

### POST `/auth/doctor/signup`

Complete doctor signup. Supports two flows:

**Normal flow (with email and OTP):**

```json
{
  "phoneNumber": "+919587733170",
  "email": "jane.smith@example.com",
  "otp": "1234",
  "name": "Dr. Jane Smith",
  "specialization": "Cardiologist",
  "experience": 10
}
```

**Experimental flow (mobile-only, no email/OTP):**

```json
{
  "phoneNumber": "+919587733170",
  "name": "Dr. Jane Smith",
  "specialization": "Cardiologist",
  "experience": 10
}
```

**Minimal experimental flow:**

```json
{
  "phoneNumber": "+919587733170"
}
```

**Note:**

- Normal flow requires `email` and `otp`. Email is verified during signup.
- Experimental flow skips email/OTP verification. All fields except `phoneNumber` are optional.
- Default slots are created automatically for the next 7 days after signup.

---

### POST `/auth/google`

Google OAuth login/signup. Auto-creates account if user doesn't exist.

```json
{
  "token": "eyJhbGciOiJSUzI1NiIsImtpZCI6Ij..."
}
```

**Note:** Token should be a Google ID token from the client-side OAuth flow.

---

### POST `/auth/session/initiate`

Create a new anonymous session for unauthenticated users. Returns a 7-day session identifier.

**No body required:**

```
POST /auth/session/initiate
```

**Response:**

```json
{
  "session_id": "session_abc123",
  "expires_at": "2025-02-05T12:00:00Z"
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
