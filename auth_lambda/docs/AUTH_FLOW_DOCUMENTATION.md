# Authentication Flow Documentation

## Overview

This document describes the complete end-to-end authentication flow for the Kokoro Doctor application. The system supports multiple authentication methods including OTP-based signup/login, experimental mobile-only signup, and Google OAuth.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Database Schema](#database-schema)
3. [User Signup Flows](#user-signup-flows)
4. [Doctor Signup Flow](#doctor-signup-flow)
5. [Login Flows](#login-flows)
6. [Google Authentication](#google-authentication)
7. [Session Management](#session-management)
8. [JWT Token Structure](#jwt-token-structure)
9. [OTP Management](#otp-management)
10. [Error Handling](#error-handling)

---

## Architecture Overview

### Components

1. **Frontend (`frontend/`)**
   - React Native/Expo application
   - `AuthContext` - Manages authentication state
   - `AuthService.js` - API communication layer
   - AsyncStorage - Local session persistence

2. **Backend (`backend/auth_lambda/`)**
   - FastAPI application running on AWS Lambda
   - Multiple routers for different auth flows
   - Services for business logic
   - DynamoDB for data persistence

### Key Tables

- **`users`** - User profiles (phone-index, email-index GSIs)
- **`doctors`** - Doctor profiles
- **`auth`** - Authentication metadata (phoneNumber PK, email-index GSI)
- **`auth_tokens`** - OTP tokens (token_id PK, phone-index GSI, email-index GSI)

---

## Database Schema

### Auth Table (`auth`)

```python
{
    "phoneNumber": str,          # Primary Key (normalized)
    "email": str,                 # Optional, indexed via GSI
    "role": str,                  # "user" | "doctor" | None
    "user_id": str,              # Optional, links to users table
    "doctor_id": str,            # Optional, links to doctors table
    "is_verified": bool,         # Overall verification status
    "email_verified": bool,       # Email verification status
    "phone_verified": bool,       # Phone verification status
    "created_at": ISO8601,       # Record creation timestamp
    "updated_at": ISO8601,       # Last update timestamp
    "last_login": ISO8601,       # Last login timestamp
    "last_verified_at": ISO8601  # Last verification timestamp
}
```

### Auth Tokens Table (`auth_tokens`)

```python
{
    "token_id": str,             # Primary Key
    "purpose": str,              # "login_otp" | "signup_otp" (Partition Key)
    "token": str,                # 4-digit OTP code
    "phoneNumber": str,          # Optional, indexed via GSI
    "email": str,                # Optional, indexed via GSI
    "role": str,                 # Optional, for signup OTPs
    "ttl": int,                  # Unix timestamp for expiration
    "createdAt": ISO8601         # Creation timestamp
}
```

---

## User Signup Flows

### Flow 1: Experimental Mobile-Only Signup

**Purpose**: Quick signup without email verification (for testing/early access)

**Endpoint**: `POST /auth/user/signup`

**Request**:
```json
{
    "phoneNumber": "+1234567890",
    "name": "John Doe"  // Optional
}
```

**Flow**:
1. Frontend calls `completeUserSignup({ phoneNumber, name })` without `email` or `otp`
2. Backend detects experimental flow (`email is None`)
3. Normalize phone number
4. Check if user exists by phone:
   - If exists → Return `400: "Phone number already registered."`
   - If not → Continue
5. Create user profile in `users` table:
   ```python
   {
       "user_id": generated_id,
       "phoneNumber": normalized_phone,
       "name": name if provided else None,
       "createdAt": timestamp
   }
   ```
6. Create/update auth record:
   ```python
   {
       "phoneNumber": normalized_phone,
       "email": None,
       "role": "user",
       "user_id": user_id,
       "is_verified": True,
       "phone_verified": True,
       "email_verified": False
   }
   ```
7. Generate JWT token
8. Return `{ access_token, user_id, message }`
9. Frontend persists token and profile to AsyncStorage

**Key Characteristics**:
- No email required
- No OTP verification
- Name is optional
- Immediate account creation
- Prevents duplicate signups by phone

---

### Flow 2: Normal Email + OTP Signup

**Purpose**: Standard signup with email verification

**Endpoint**: `POST /auth/user/signup`

**Request**:
```json
{
    "phoneNumber": "+1234567890",
    "email": "user@example.com",
    "otp": "1234",
    "name": "John Doe"
}
```

**Flow**:
1. Frontend requests OTP:
   - `POST /auth/user/request-signup-otp`
   - `{ phoneNumber, email, role: "user" }`
2. Backend validates phone/email, checks for existing accounts
3. OTP sent to **email only** (not SMS)
4. OTP stored in `auth_tokens` table with 5-minute TTL
5. User enters OTP
6. Frontend calls `completeUserSignup({ phoneNumber, email, otp, name })`
7. Backend validates OTP:
   - Lookup by email + purpose="signup_otp"
   - Check expiration (TTL)
   - Verify OTP code matches
   - Delete token after validation
8. Check for existing users:
   - By phone → `400: "Phone number already registered."`
   - By email → `400: "Email already registered."`
9. Create user profile:
   ```python
   {
       "user_id": generated_id,
       "phoneNumber": normalized_phone,
       "email": normalized_email,
       "name": name,
       "createdAt": timestamp
   }
   ```
10. Create/update auth record:
    ```python
    {
        "phoneNumber": normalized_phone,
        "email": normalized_email,
        "role": "user",
        "user_id": user_id,
        "is_verified": True,
        "email_verified": True,
        "phone_verified": False
    }
    ```
11. Generate JWT token
12. Return `{ access_token, user_id, message }`
13. Frontend fetches user profile if not in response
14. Frontend persists token and profile

**Key Characteristics**:
- Email required
- OTP verification required
- Name required
- Email verified during signup
- Phone not verified during signup

---

## Doctor Signup Flows

### Flow 1: Experimental Mobile-Only Signup

**Purpose**: Quick doctor signup without email verification (for testing/early access)

**Endpoint**: `POST /auth/doctor/signup`

**Request**:
```json
{
    "phoneNumber": "+1234567890",
    "name": "Dr. Jane Smith",  // Optional
    "specialization": "Cardiology",  // Optional
    "experience": 10  // Optional
}
```

**Flow**:
1. Frontend calls `completeDoctorSignup({ phoneNumber, name?, specialization?, experience? })` without `email` or `otp`
2. Backend detects experimental flow (`email is None`)
3. Normalize phone number
4. Check if doctor exists by phone:
   - If exists → Return `400: "Phone number already registered."`
   - If not → Continue
5. Create doctor profile in `doctors` table:
   ```python
   {
       "doctor_id": generated_id,
       "phoneNumber": normalized_phone,
       "doctorname": name if provided else None,
       "specialization": specialization if provided else None,
       "experience": experience if provided else None,
       "createdAt": timestamp
   }
   ```
6. Create/update auth record:
   ```python
   {
       "phoneNumber": normalized_phone,
       "email": None,
       "role": "doctor",
       "doctor_id": doctor_id,
       "user_id": None,
       "is_verified": True,
       "phone_verified": True,  # Phone is the primary identifier in experimental flow
       "email_verified": False
   }
   ```
7. Generate JWT token
8. Return `{ access_token, doctor_id, message }`
9. Frontend fetches doctor profile if not in response
10. Frontend persists token and profile

**Key Characteristics**:
- No email required
- No OTP verification
- Name, specialization, experience are optional
- Immediate account creation
- Prevents duplicate signups by phone

---

### Flow 2: Normal Email + OTP Signup

**Purpose**: Standard doctor registration with email verification

**Endpoint**: `POST /auth/doctor/signup`

**Request**:
```json
{
    "phoneNumber": "+1234567890",
    "email": "doctor@example.com",
    "otp": "1234",
    "name": "Dr. Jane Smith",
    "specialization": "Cardiology",
    "experience": 10
}
```

**Flow**:
1. Frontend requests OTP:
   - `POST /auth/doctor/request-signup-otp`
   - `{ phoneNumber, email, role: "doctor" }`
2. Backend validates phone/email, checks for existing accounts
3. OTP sent to **email only** (not SMS)
4. OTP stored in `auth_tokens` table with 5-minute TTL
5. User enters OTP
6. Frontend calls `completeDoctorSignup({ phoneNumber, email, otp, name, specialization, experience })`
7. Backend validates OTP:
   - Lookup by email + purpose="signup_otp"
   - Check expiration (TTL)
   - Verify OTP code matches
   - Delete token after validation
8. Check for existing doctors:
   - By phone → `400: "Phone number already registered."`
   - By email in auth table → `400: "Email already registered."`
9. Create doctor profile in `doctors` table:
   ```python
   {
       "doctor_id": generated_id,
       "phoneNumber": normalized_phone,
       "email": normalized_email,
       "doctorname": name,
       "specialization": specialization,
       "experience": experience,
       "createdAt": timestamp
   }
   ```
10. Create/update auth record:
    ```python
    {
        "phoneNumber": normalized_phone,
        "email": normalized_email,
        "role": "doctor",
        "doctor_id": doctor_id,
        "user_id": None,
        "is_verified": True,
        "email_verified": True,  # Email verified during signup
        "phone_verified": False  # Phone not verified during signup
    }
    ```
11. Generate JWT token
12. Return `{ access_token, doctor_id, message }`
13. Frontend fetches doctor profile if not in response
14. Frontend persists token and profile

**Key Characteristics**:
- Email required
- OTP verification required
- Name, specialization, experience required
- Email verified during signup
- Phone not verified during signup

---

## Login Flows

### Flow 1: Login Discovery (No OTP)

**Purpose**: Discover user account and role

**Endpoint**: `POST /auth/login`

**Request**:
```json
{
    "identifier": "user@example.com"  // or phone number
}
```

**Flow**:
1. Frontend calls `initiateLogin({ identifier })`
2. Backend detects identifier type (email vs phone)
3. Lookup auth record:
   - By email → query `auth` table via email-index GSI
   - By phone → query `auth` table by phoneNumber PK
4. If not found or no account → `404: "Please sign up first."`
5. Return discovery response:
   ```json
   {
       "role": "user" | "doctor",
       "message": "OTP required to continue."
   }
   ```
6. Frontend requests OTP based on role

---

### Flow 2: Login with OTP

**Purpose**: Authenticate existing user with OTP

**Endpoint**: `POST /auth/login`

**Request**:
```json
{
    "identifier": "user@example.com",  // or phone number
    "otp": "1234"
}
```

**Flow**:
1. Frontend requests OTP:
   - `POST /auth/request-otp`
   - `{ identifier, preferredChannel: "email" | "sms" }`
2. Backend detects identifier type
3. Lookup auth record
4. Determine OTP channel:
   - If SMS requested but phone not verified → fallback to email
   - Default: email
5. Send OTP to preferred channel
6. OTP stored in `auth_tokens` with 5-minute TTL
7. User enters OTP
8. Frontend calls `loginWithOtp({ identifier, otp })`
9. Backend validates OTP:
   - Lookup by identifier (email or phone) + purpose="login_otp"
   - Check expiration
   - Verify OTP code
   - Delete token after validation
10. Update auth record:
    ```python
    {
        "is_verified": True,
        "last_login": timestamp,
        "last_verified_at": timestamp
    }
    ```
11. Generate JWT token
12. Return `{ access_token, user_id | doctor_id, role, verified: true }`
13. Frontend fetches profile if not in response
14. Frontend persists token and profile

**Key Characteristics**:
- Works with email or phone number
- User can choose email or SMS (default: email)
- SMS only if phone is verified
- Updates last_login timestamp

---

## Google Authentication

**Purpose**: Sign in with Google OAuth (Web only)

**Endpoint**: `POST /auth/google` (currently commented out)

**Request**:
```json
{
    "token": "google_id_token"
}
```

**Flow**:
1. Frontend initiates Google OAuth flow (web only)
2. User authenticates with Google
3. Frontend receives Google ID token
4. Frontend calls backend with token
5. Backend verifies token with Google:
   - Extract email, name, picture
   - Verify token signature
6. Lookup user by email:
   - If exists → Update profile (name, picture if changed)
   - If not → Create new user:
     ```python
     {
         "user_id": generated_id,
         "name": name or "Unknown User",
         "username": name,
         "email": email,
         "phoneNumber": None,
         "picture": picture,
         "authProvider": "google",
         "emailVerified": True
     }
     ```
7. If phone exists → Update auth record
8. Generate JWT token (uses `google:{user_id}` as phone if no phone)
9. Return `{ access_token, profile, role: "user", message }`
10. Frontend persists token and profile

**Key Characteristics**:
- Web only (not available on native)
- Email is primary identifier
- Phone optional
- Auto-creates account if not exists
- Updates existing account with Google info

---

## Session Management

### Frontend Session Persistence

**Storage Keys**:
- `@token` - JWT access token
- `@user` - User/Doctor profile (JSON stringified)
- `userRole` - User role ("user" | "doctor")
- `session_id` - Anonymous session ID (for unauthenticated users)

**Functions**:
- `persistUserSession({ access_token, profile, role })` - Save auth state
- `restoreUserState()` - Restore auth state on app start
- `logOut()` - Clear all auth data

**Flow**:
1. On app start:
   - `AuthContext` calls `restoreUserState()`
   - Reads token, user, role from AsyncStorage
   - Sets React state
   - Identifies user in Mixpanel (analytics)
2. On login/signup:
   - Save token and profile
   - Set role
   - Clear anonymous session
   - Reset chat count
3. On logout:
   - Remove all auth keys
   - Clear session
   - Reset state

### Anonymous Sessions

**Purpose**: Track unauthenticated users

**Endpoint**: `POST /auth/session/initiate`

**Flow**:
1. Frontend calls `getSessionId()` from `sessionManager.js`
2. Check if user is logged in → return `null`
3. Check if session exists locally → return existing
4. Call backend to create session
5. Backend generates session_id with 7-day TTL
6. Store in `sessions` table (if configured)
7. Return `{ session_id }`
8. Frontend saves locally
9. Session cleared on login/logout

---

## JWT Token Structure

**Algorithm**: HS256 (configurable)

**Payload**:
```json
{
    "sub": "user_id or doctor_id or phone_number",
    "iss": "kokoro-doctor",
    "iat": 1234567890,           // Issued at (Unix timestamp)
    "exp": 1234567890,            // Expiration (Unix timestamp)
    "role": "user" | "doctor",
    "phoneNumber": "+1234567890",
    "user_id": "user_123",        // Optional
    "doctor_id": "doctor_456"     // Optional
}
```

**Expiration**: Configurable (default from `JWT_EXP_MINUTES`)

**Usage**:
- Sent in `Authorization: Bearer <token>` header
- Backend services validate token and extract user_id/doctor_id
- Used for authorization checks

---

## OTP Management

### OTP Generation

- **Format**: 4-digit numeric code (1000-9999)
- **TTL**: 5 minutes
- **Storage**: `auth_tokens` table

### OTP Purposes

1. **`signup_otp`**: For account creation
   - Sent to email only
   - Validated during signup
   - Deleted after validation

2. **`login_otp`**: For login
   - Sent to email (default) or SMS
   - Validated during login
   - Deleted after validation

### OTP Lookup

- **By Email**: Query `auth_tokens` via email-index GSI
- **By Phone**: Query `auth_tokens` via phone-index GSI
- **Most Recent**: Sort by `createdAt` descending, return first non-expired

### Rate Limiting

- Rate limiting applied per phone number
- Prevents OTP spam
- Uses `rate_limit_guard` utility

---

## Error Handling

### Common Errors

1. **400 Bad Request**:
   - Invalid phone number
   - Invalid email format
   - Missing required fields
   - Invalid OTP
   - OTP expired
   - Account already exists

2. **404 Not Found**:
   - User not registered
   - Account not found

3. **500 Internal Server Error**:
   - Database errors
   - OTP send failures
   - Unexpected exceptions

### Error Response Format

```json
{
    "detail": "Error message"
}
```

Or structured:
```json
{
    "detail": {
        "error": "ERROR_CODE",
        "message": "Human-readable message"
    }
}
```

### Frontend Error Handling

- `getErrorMessage(error)` - Extracts error message
- `createApiError()` - Creates structured error
- Errors displayed to user via UI
- Analytics tracking for failed auth attempts

---

## Flow Diagrams

### User Signup (Experimental)

```
User → Frontend: Enter phone (+ name)
Frontend → Backend: POST /auth/user/signup { phoneNumber, name? }
Backend: Check if user exists
  ├─ Exists → Return 400
  └─ Not exists → Create user profile
Backend: Create auth record
Backend: Generate JWT
Backend → Frontend: { access_token, user_id }
Frontend: Save to AsyncStorage
Frontend: Navigate to app
```

### User Signup (Normal)

```
User → Frontend: Enter phone, email, name
Frontend → Backend: POST /auth/user/request-signup-otp { phoneNumber, email }
Backend: Validate & check existing accounts
Backend: Generate OTP
Backend: Send OTP to email
Backend → Frontend: { message: "OTP sent" }
User → Frontend: Enter OTP
Frontend → Backend: POST /auth/user/signup { phoneNumber, email, otp, name }
Backend: Validate OTP
Backend: Check for duplicates
Backend: Create user profile
Backend: Create auth record
Backend: Generate JWT
Backend → Frontend: { access_token, user_id }
Frontend: Fetch user profile
Frontend: Save to AsyncStorage
Frontend: Navigate to app
```

### Doctor Signup (Experimental)

```
User → Frontend: Enter phone (+ name, specialization, experience)
Frontend → Backend: POST /auth/doctor/signup { phoneNumber, name?, specialization?, experience? }
Backend: Check if doctor exists
  ├─ Exists → Return 400
  └─ Not exists → Create doctor profile
Backend: Create auth record
Backend: Generate JWT
Backend → Frontend: { access_token, doctor_id }
Frontend: Save to AsyncStorage
Frontend: Navigate to app
```

### Doctor Signup (Normal)

```
User → Frontend: Enter phone, email, name, specialization, experience
Frontend → Backend: POST /auth/doctor/request-signup-otp { phoneNumber, email }
Backend: Validate & check existing accounts
Backend: Generate OTP
Backend: Send OTP to email
Backend → Frontend: { message: "OTP sent" }
User → Frontend: Enter OTP
Frontend → Backend: POST /auth/doctor/signup { phoneNumber, email, otp, name, specialization, experience }
Backend: Validate OTP
Backend: Check for duplicates
Backend: Create doctor profile
Backend: Create auth record
Backend: Generate JWT
Backend → Frontend: { access_token, doctor_id }
Frontend: Fetch doctor profile
Frontend: Save to AsyncStorage
Frontend: Navigate to app
```

### Login Flow

```
User → Frontend: Enter email/phone
Frontend → Backend: POST /auth/login { identifier }
Backend: Lookup auth record
Backend → Frontend: { role, message: "OTP required" }
Frontend → Backend: POST /auth/request-otp { identifier, preferredChannel }
Backend: Send OTP (email or SMS)
Backend → Frontend: { message: "OTP sent" }
User → Frontend: Enter OTP
Frontend → Backend: POST /auth/login { identifier, otp }
Backend: Validate OTP
Backend: Update auth record (last_login)
Backend: Generate JWT
Backend → Frontend: { access_token, user_id/doctor_id, role }
Frontend: Fetch profile
Frontend: Save to AsyncStorage
Frontend: Navigate to app
```

---

## Security Considerations

1. **Phone Number Normalization**: All phone numbers normalized before storage/lookup
2. **Email Normalization**: All emails lowercased and trimmed
3. **OTP Expiration**: 5-minute TTL prevents replay attacks
4. **OTP Deletion**: Tokens deleted after validation
5. **Rate Limiting**: Prevents OTP spam
6. **JWT Expiration**: Tokens expire after configured time
7. **HTTPS Only**: All API calls should use HTTPS
8. **Token Storage**: Tokens stored securely in AsyncStorage (encrypted on device)

---

## Testing Scenarios

### User Signup (Experimental)
- ✅ New user with phone + name
- ✅ New user with phone only
- ✅ Duplicate phone number → Error
- ✅ Invalid phone format → Error

### User Signup (Normal)
- ✅ New user with valid OTP
- ✅ Expired OTP → Error
- ✅ Invalid OTP → Error
- ✅ Duplicate phone → Error
- ✅ Duplicate email → Error

### Doctor Signup (Experimental)
- ✅ New doctor with phone + name
- ✅ New doctor with phone + name + specialization + experience
- ✅ New doctor with phone only
- ✅ Duplicate phone number → Error
- ✅ Invalid phone format → Error

### Doctor Signup (Normal)
- ✅ New doctor with valid OTP
- ✅ Expired OTP → Error
- ✅ Invalid OTP → Error
- ✅ Duplicate phone → Error
- ✅ Duplicate email → Error

### Login
- ✅ Login with email + valid OTP
- ✅ Login with phone + valid OTP
- ✅ Unregistered email → Error
- ✅ Expired OTP → Error
- ✅ Invalid OTP → Error

---

## API Endpoints Summary

### Signup
- `POST /auth/user/request-signup-otp` - Request signup OTP (email only)
- `POST /auth/user/signup` - Complete user signup
- `POST /auth/doctor/request-signup-otp` - Request doctor signup OTP (email only)
- `POST /auth/doctor/signup` - Complete doctor signup

### Login
- `POST /auth/login` - Login (discovery or with OTP)
- `POST /auth/request-otp` - Request login OTP (email or SMS)

### Session
- `POST /auth/session/initiate` - Create anonymous session

### Google Auth
- `POST /auth/google` - Google OAuth (currently disabled)

---

## Notes

- Experimental flow is designed for quick testing/early access (available for both users and doctors)
- Normal flow provides better security with email verification
- Doctor signup supports both experimental (mobile-only) and normal (email + OTP) flows
- Phone verification is separate from email verification
- Anonymous sessions help track unauthenticated usage
- All timestamps are in ISO8601 format (UTC)

---

## Future Enhancements

- [ ] Phone number verification via SMS
- [ ] Two-factor authentication
- [ ] Password-based login (if needed)
- [ ] Social login (Facebook, Apple)
- [ ] Account recovery flows
- [ ] Email verification resend
- [ ] Session refresh tokens

---
