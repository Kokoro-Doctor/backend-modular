# Complete Authentication Flow - Function Call Sequence

This document traces the complete authentication flow, showing exactly how each function calls the next, from user input to final login/signup completion.

---

## Table of Contents

1. [Login Flow (Normal - With OTP)](#login-flow-normal---with-otp)
2. [Login Flow (Experimental - Without OTP)](#login-flow-experimental---without-otp)
3. [Signup Flow (Normal - With OTP)](#signup-flow-normal---with-otp)
4. [Signup Flow (Experimental - Without OTP)](#signup-flow-experimental---without-otp)

---

## Login Flow (Normal - With OTP)

### Step 1: User Enters Identifier (Email/Phone)

**Frontend:** `PatientAuthModal.jsx` or similar component
- User enters email or phone number
- Clicks "Login" button

---

### Step 2: Initiate Login (Discovery)

**Frontend Call:**
```javascript
// frontend/contexts/AuthContext.js
initiateLoginHandler({ identifier: "user@example.com" })
  ↓
// frontend/utils/AuthService.js
initiateLogin({ identifier: "user@example.com" })
  ↓
POST /auth/login
{ identifier: "user@example.com" }
```

**Backend Processing:**
```python
# backend/auth_lambda/app/routers/auth_common.py
@router.post("/login")
def login(data: schemas.LoginRequest):
    return handle_login(data.identifier, data.otp)  # otp is None
  ↓
# backend/auth_lambda/app/services/auth_service.py
def handle_login(identifier: str, otp: Optional[str] = None):
    # 1. Detect identifier type (email vs phone)
    is_email = "@" in identifier
    
    # 2. Lookup auth record
    if is_email:
        record = get_auth_record_by_email(normalized_email)
    else:
        record = get_auth_record(normalized_phone)
    
    # 3. Determine role
    role = record.get("role")  # "user" or "doctor"
    
    # 4. Check if experimental flow (no email)
    has_email = bool(record.get("email"))
    is_experimental_flow = not has_email
    
    # 5. Since otp is None and NOT experimental flow:
    if not otp and not is_experimental_flow:
        return _login_discovery_response(role, record)
```

**Discovery Response:**
```json
{
    "role": "user",
    "message": "OTP required to continue.",
    "otp_required": true
}
```

**Frontend Receives:**
```javascript
// frontend/utils/AuthService.js
// initiateLogin returns discovery response
return data;  // { role: "user", message: "...", otp_required: true }

// frontend/contexts/AuthContext.js
// initiateLoginHandler receives discovery response
return result;  // Returns to UI component
```

---

### Step 3: Frontend Shows OTP Input UI

**Frontend:** `PatientAuthModal.jsx`
- Receives discovery response
- Shows OTP input field
- User clicks "Request OTP" or OTP is auto-requested

---

### Step 4: Request OTP

**Frontend Call:**
```javascript
// frontend/components/Auth/PatientAuthModal.jsx
requestLoginOtpHandler({
    identifier: "user@example.com",
    preferredChannel: "email"
})
  ↓
// frontend/contexts/AuthContext.js
requestLoginOtpHandler(payload)
  ↓
// frontend/utils/AuthService.js
requestLoginOtp({ identifier, preferredChannel })
  ↓
POST /auth/request-otp
{
    identifier: "user@example.com",
    preferredChannel: "email"
}
```

**Backend Processing:**
```python
# backend/auth_lambda/app/routers/auth_common.py
@router.post("/request-otp")
def request_otp(data: schemas.LoginOtpRequest):
    return handle_login_otp_request(data.identifier, data.preferredChannel)
  ↓
# backend/auth_lambda/app/services/auth_service.py
def handle_login_otp_request(identifier: str, preferred_channel: str = "email"):
    # 1. Detect identifier type
    is_email = "@" in identifier
    
    # 2. Lookup auth record
    if is_email:
        record = get_auth_record_by_email(normalized_email)
        phone_number = record.get("phoneNumber")
    else:
        record = get_auth_record(normalized_phone)
        email = record.get("email")
    
    # 3. Determine OTP channel (email or SMS)
    if preferred_channel == "sms":
        if not record.get("phone_verified", False):
            preferred_channel = "email"  # Fallback to email
    
    # 4. Send OTP
    with rate_limit_guard(RateLimitAction.MOBILE_OTP, phone_number):
        dispatch_otp(phone_number, email, LOGIN_OTP_PURPOSE, preferred_channel, role)
  ↓
def dispatch_otp(phone_number, email, purpose, preferred_channel, role):
    # 1. Generate 4-digit OTP
    otp = f"{random.randint(1000, 9999)}"
    
    # 2. Create token record
    token_item = {
        "token_id": generate_token_id(),
        "purpose": "login_otp",
        "token": otp,
        "phoneNumber": phone_number,
        "email": email,
        "ttl": ttl_minutes_from_now(5),  # 5 minute expiration
        "createdAt": timestamp
    }
    
    # 3. Send OTP via email or SMS
    if preferred_channel == "email":
        send_otp_email(email, otp)
    else:
        send_otp_sms(phone_number, otp)
    
    # 4. Store token in auth_tokens table
    config.auth_tokens_table.put_item(Item=token_item)
```

**Response:**
```json
{
    "message": "OTP sent successfully."
}
```

**Frontend:**
- Shows "OTP sent to your email" message
- Displays OTP input field
- Starts 60-second countdown timer

---

### Step 5: User Enters OTP

**Frontend:** `PatientAuthModal.jsx`
- User enters 4-digit OTP
- Clicks "Verify & Login"

---

### Step 6: Login with OTP

**Frontend Call:**
```javascript
// frontend/components/Auth/PatientAuthModal.jsx
loginWithOtpHandler({
    identifier: "user@example.com",
    otp: "1234"
})
  ↓
// frontend/contexts/AuthContext.js
loginWithOtpHandler(payload)
  ↓
// frontend/utils/AuthService.js
loginWithOtp({ identifier, otp })
  ↓
POST /auth/login
{
    identifier: "user@example.com",
    otp: "1234"
}
```

**Backend Processing:**
```python
# backend/auth_lambda/app/routers/auth_common.py
@router.post("/login")
def login(data: schemas.LoginRequest):
    return handle_login(data.identifier, data.otp)  # otp is "1234"
  ↓
# backend/auth_lambda/app/services/auth_service.py
def handle_login(identifier: str, otp: Optional[str] = None):
    # 1. Lookup auth record (same as Step 2)
    record = get_auth_record_by_email(normalized_email)
    phone_number = record.get("phoneNumber")
    role = record.get("role")
    
    # 2. OTP is provided, so proceed with OTP validation
    otp_clean = otp.strip()
    return login_with_otp(phone_number, record, otp_clean, identifier)
  ↓
def login_with_otp(phone_number: str, record: dict, otp: str, identifier: str):
    # 1. Validate OTP
    validate_otp(identifier, otp, LOGIN_OTP_PURPOSE)
  ↓
def validate_otp(identifier: str, otp: str, purpose: str):
    # 1. Detect identifier type
    is_email = "@" in identifier
    
    # 2. Lookup token from auth_tokens table
    if is_email:
        token_item = get_auth_token_by_email(normalized_email, "login_otp")
    else:
        token_item = get_auth_token_by_phone(normalized_phone, "login_otp")
    
    # 3. Check expiration
    if token_item.get("ttl") < current_timestamp:
        raise HTTPException("OTP expired")
    
    # 4. Verify OTP code matches
    if token_item["token"] != otp:
        raise HTTPException("Invalid OTP")
    
    # 5. Delete token after validation
    _delete_token(token_item["token_id"], "login_otp")
    
    return token_item  # Validation successful
  ↓
# Back to login_with_otp
def login_with_otp(...):
    # 2. Update auth record
    update_auth_record(phone_number, {
        "is_verified": True,
        "last_login": timestamp,
        "last_verified_at": timestamp
    })
    
    # 3. Generate JWT token
    return _issue_login_response(role, phone_number, record)
  ↓
def _issue_login_response(role: str, phone_number: str, record: dict):
    # 1. Extract user_id or doctor_id
    user_id = record.get("user_id")
    doctor_id = record.get("doctor_id")
    
    # 2. Create JWT token
    access_token = create_jwt(
        phone_number=phone_number,
        role=role,
        user_id=user_id,
        doctor_id=doctor_id
    )
    
    # 3. Return response
    return {
        "verified": True,
        "role": role,
        "access_token": access_token,
        "user_id": user_id,  # or doctor_id
        "message": "Logged in successfully."
    }
```

**Response:**
```json
{
    "verified": true,
    "role": "user",
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "user_id": "user_123",
    "message": "Logged in successfully."
}
```

---

### Step 7: Frontend Processes Login Response

**Frontend Processing:**
```javascript
// frontend/utils/AuthService.js
loginWithOtp({ identifier, otp })
  ↓
const data = await postJson("/auth/login", { identifier, otp })
return handleLoginResponse(data)
  ↓
const handleLoginResponse = async (data) => {
    // 1. Save token immediately
    await AsyncStorage.setItem("@token", data.access_token)
    
    // 2. Fetch user profile if not in response
    let profile = data.profile
    if (!profile && data.user_id) {
        profile = await fetchUserProfile(data.user_id, data.access_token)
        // GET /users/{user_id}
        // Returns: { user: { user_id, name, email, phoneNumber, ... } }
    }
    
    // 3. Persist session
    await persistUserSession({
        access_token: data.access_token,
        profile: profile,
        role: data.role
    })
    // Saves to AsyncStorage:
    // - @token: access_token
    // - @user: JSON.stringify(profile)
    // - userRole: role
    
    return { ...data, profile }
}
  ↓
// frontend/contexts/AuthContext.js
loginWithOtpHandler(payload)
  ↓
const result = await loginWithOtpApi(payload)
return await syncSession(result)
  ↓
const syncSession = async (result, fallbackRole = null) => {
    // 1. Set React state
    setUser(result.profile)
    setRole(result.role)
    
    // 2. Clear session and reset chat count
    await clearSession()
    await resetChatCount()
    
    // 3. Identify user in Mixpanel (analytics)
    mixpanel.identify(userId, { name, email, phone, role })
    mixpanel.track("User Logged In", { user_id, role, login_method: "otp" })
    
    return result
}
```

---

### Step 8: Navigation

**Frontend:** `PatientAuthModal.jsx`
```javascript
// After loginWithOtpHandler completes
const userRole = result?.role

if (userRole === "doctor") {
    navigation.reset({
        index: 0,
        routes: [{ name: "DoctorAppNavigation", params: { screen: "Dashboard" } }]
    })
} else if (userRole === "user") {
    navigation.navigate("LandingPage")
}
```

---

## Login Flow (Experimental - Without OTP)

### Step 1: User Enters Phone Number

**Frontend:** User enters phone number (no email)

---

### Step 2: Initiate Login

**Frontend Call:**
```javascript
initiateLogin({ identifier: "+1234567890" })
  ↓
POST /auth/login
{ identifier: "+1234567890" }
```

**Backend Processing:**
```python
# backend/auth_lambda/app/services/auth_service.py
def handle_login(identifier: str, otp: Optional[str] = None):
    # 1. Lookup auth record by phone
    record = get_auth_record(normalized_phone)
    
    # 2. Check if experimental flow (no email)
    has_email = bool(record.get("email"))
    is_experimental_flow = not has_email  # True
    
    # 3. Since otp is None AND experimental flow:
    if not otp and is_experimental_flow:
        # Update last_login timestamp
        update_auth_record(phone_number, {
            "last_login": timestamp,
            "updated_at": timestamp
        })
        
        # Generate JWT and login directly
        return _issue_login_response(role, phone_number, record)
```

**Response:**
```json
{
    "verified": true,
    "role": "user",
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "user_id": "user_123",
    "message": "Logged in successfully."
}
```

**Frontend Processing:**
```javascript
// frontend/utils/AuthService.js
initiateLogin({ identifier })
  ↓
const data = await postJson("/auth/login", { identifier })

// Check if access_token is present (direct login)
if (data?.access_token) {
    return handleLoginResponse(data)  // Same as Step 7 above
}

// Otherwise return discovery response
return data
  ↓
// frontend/contexts/AuthContext.js
initiateLoginHandler(payload)
  ↓
const result = await initiateLoginApi(payload)

// If result has access_token, sync session
if (result?.access_token) {
    return await syncSession(result)  // Same as Step 7 above
}

// Otherwise return discovery response
return result
```

**Result:** User is logged in immediately without OTP!

---

## Signup Flow (Normal - With OTP)

### Step 1: User Enters Details

**Frontend:** User enters phone, email, name

---

### Step 2: Request Signup OTP

**Frontend Call:**
```javascript
requestSignupOtpHandler({
    phoneNumber: "+1234567890",
    email: "user@example.com",
    role: "user"
})
  ↓
POST /auth/user/request-signup-otp
{
    phoneNumber: "+1234567890",
    email: "user@example.com"
}
```

**Backend Processing:**
```python
# backend/auth_lambda/app/routers/auth_user.py
@router.post("/user/request-signup-otp")
def request_user_signup_otp(data: schemas.SignupOtpRequest):
    return handle_signup_otp_request(data.phoneNumber, data.email, "user")
  ↓
# backend/auth_lambda/app/services/auth_service.py
def handle_signup_otp_request(phone_number: str, email: str, role: str):
    # 1. Check if account already exists
    record = get_auth_record(normalized_phone)
    if record and _record_has_account(record):
        raise HTTPException("Account already exists")
    
    # 2. Ensure auth record exists
    ensure_auth_record(normalized_phone, normalized_email)
    
    # 3. Send OTP to email ONLY (not SMS)
    with rate_limit_guard(RateLimitAction.MOBILE_OTP, normalized_phone):
        dispatch_otp(normalized_phone, normalized_email, SIGNUP_OTP_PURPOSE, "email", role)
```

**Response:**
```json
{
    "message": "OTP sent successfully to your email address."
}
```

---

### Step 3: User Enters OTP

**Frontend:** User enters 4-digit OTP

---

### Step 4: Complete Signup

**Frontend Call:**
```javascript
completeUserSignup({
    phoneNumber: "+1234567890",
    email: "user@example.com",
    otp: "1234",
    name: "John Doe"
})
  ↓
POST /auth/user/signup
{
    phoneNumber: "+1234567890",
    email: "user@example.com",
    otp: "1234",
    name: "John Doe"
}
```

**Backend Processing:**
```python
# backend/auth_lambda/app/routers/auth_user.py
@router.post("/user/signup")
def user_signup(data: schemas.UserProfileCreate):
    # 1. Detect flow (email is provided, so NOT experimental)
    is_experimental_flow = data.email is None  # False
    
    # 2. Normal flow
    normalized_email = data.email.lower().strip()
    
    # 3. Validate OTP
    validate_otp(normalized_email, data.otp, SIGNUP_OTP_PURPOSE)
    
    # 4. Check for duplicates
    if user_exists_by_phone(normalized_phone):
        raise HTTPException("Phone already registered")
    if user_exists_by_email(normalized_email):
        raise HTTPException("Email already registered")
    
    # 5. Create user profile
    user_item = create_user_profile(
        {"name": data.name, "email": data.email},
        normalized_phone
    )
    # Creates record in users table
    
    # 6. Update auth record
    update_auth_record(normalized_phone, {
        "role": "user",
        "user_id": user_item["user_id"],
        "is_verified": True,
        "email_verified": True,
        "phone_verified": False
    })
    
    # 7. Generate JWT
    access_token = create_jwt(
        phone_number=normalized_phone,
        role="user",
        user_id=user_item["user_id"]
    )
    
    return {
        "message": "User profile created successfully.",
        "access_token": access_token,
        "user_id": user_item["user_id"]
    }
```

**Frontend Processing:**
```javascript
// Same as login - fetch profile and persist session
const data = await postJson("/auth/user/signup", {...})
let profile = await fetchUserProfile(data.user_id, data.access_token)
await persistUserSession({ access_token, profile, role: "user" })
```

---

## Signup Flow (Experimental - Without OTP)

### Step 1: User Enters Phone (Optional Name)

**Frontend:** User enters phone number, optionally name

---

### Step 2: Complete Signup (No OTP)

**Frontend Call:**
```javascript
completeUserSignup({
    phoneNumber: "+1234567890",
    name: "John Doe"  // Optional
    // No email, no otp
})
  ↓
POST /auth/user/signup
{
    phoneNumber: "+1234567890",
    name: "John Doe"
}
```

**Backend Processing:**
```python
# backend/auth_lambda/app/routers/auth_user.py
@router.post("/user/signup")
def user_signup(data: schemas.UserProfileCreate):
    # 1. Detect experimental flow
    is_experimental_flow = data.email is None  # True
    
    # 2. Check if user exists
    existing_user = get_user_by_phone(normalized_phone)
    if existing_user:
        raise HTTPException("Phone already registered")
    
    # 3. Create user profile (no email)
    user_data = {}
    if data.name:
        user_data["name"] = data.name
    
    user_item = create_user_profile(user_data, normalized_phone)
    
    # 4. Update auth record
    update_auth_record(normalized_phone, {
        "role": "user",
        "user_id": user_item["user_id"],
        "is_verified": True,
        "phone_verified": True,
        "email_verified": False
    })
    
    # 5. Generate JWT
    access_token = create_jwt(...)
    
    return {
        "message": "User profile created successfully.",
        "access_token": access_token,
        "user_id": user_item["user_id"]
    }
```

**Frontend Processing:**
```javascript
// Same as normal signup - fetch profile and persist session
```

---

## Key Function Call Chains

### Login Discovery → OTP Request → Login with OTP

```
Frontend:
initiateLogin() 
  → POST /auth/login { identifier }
    → handle_login(identifier, otp=None)
      → _login_discovery_response(role, record)
        → Returns: { role, message, otp_required }

Frontend:
requestLoginOtp()
  → POST /auth/request-otp { identifier, preferredChannel }
    → handle_login_otp_request(identifier, preferred_channel)
      → dispatch_otp(phone, email, "login_otp", channel, role)
        → send_otp_email() or send_otp_sms()
        → Store token in auth_tokens table

Frontend:
loginWithOtp()
  → POST /auth/login { identifier, otp }
    → handle_login(identifier, otp="1234")
      → login_with_otp(phone, record, otp, identifier)
        → validate_otp(identifier, otp, "login_otp")
          → Lookup token, verify, delete
        → update_auth_record() [update last_login]
        → _issue_login_response(role, phone, record)
          → create_jwt(phone, role, user_id)
          → Returns: { access_token, user_id, role }
```

### Experimental Login (No OTP)

```
Frontend:
initiateLogin()
  → POST /auth/login { identifier }
    → handle_login(identifier, otp=None)
      → Check: is_experimental_flow = True
      → update_auth_record() [update last_login]
      → _issue_login_response(role, phone, record)
        → create_jwt()
        → Returns: { access_token, user_id, role }
```

---

## Database Operations

### Auth Table Updates

**During Login:**
```python
update_auth_record(phone_number, {
    "last_login": "2024-01-01T12:00:00Z",
    "updated_at": "2024-01-01T12:00:00Z",
    "is_verified": True,  # For OTP login
    "last_verified_at": "2024-01-01T12:00:00Z"  # For OTP login
})
```

**During Signup:**
```python
update_auth_record(phone_number, {
    "role": "user" | "doctor",
    "user_id": "user_123" | None,
    "doctor_id": "doctor_456" | None,
    "is_verified": True,
    "email_verified": True | False,
    "phone_verified": True | False
})
```

### Token Table Operations

**OTP Storage:**
```python
auth_tokens_table.put_item(Item={
    "token_id": "uuid",
    "purpose": "login_otp" | "signup_otp",
    "token": "1234",
    "phoneNumber": "+1234567890",
    "email": "user@example.com",
    "ttl": 1704110400,  # Unix timestamp (5 min from now)
    "createdAt": "2024-01-01T12:00:00Z"
})
```

**OTP Validation:**
```python
# Lookup token
token_item = get_auth_token_by_email(email, "login_otp")

# Verify expiration
if token_item["ttl"] < current_timestamp:
    raise "OTP expired"

# Verify code
if token_item["token"] != otp:
    raise "Invalid OTP"

# Delete after validation
_delete_token(token_id, "login_otp")
```

---

## Summary

### Normal Flow (With OTP):
1. **Discovery**: Check account exists, get role → `{ role, otp_required: true }`
2. **Request OTP**: Send OTP to email/SMS → Store token → `{ message: "OTP sent" }`
3. **Login**: Validate OTP → Delete token → Update auth → Generate JWT → `{ access_token, user_id }`
4. **Frontend**: Fetch profile → Persist session → Navigate

### Experimental Flow (Without OTP):
1. **Login**: Check account exists → Update auth → Generate JWT → `{ access_token, user_id }`
2. **Frontend**: Fetch profile → Persist session → Navigate

The key difference: Experimental flow skips OTP validation and goes straight to JWT generation!
