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
