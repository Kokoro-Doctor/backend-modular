# Authentication Flow Folder Structure

```
kokoro-codebase/
│
├── backend/
│   └── auth_lambda/
│       └── app/
│           ├── __init__.py
│           ├── config.py
│           ├── logger.py
│           ├── main.py
│           │
│           ├── models/
│           │   ├── __init__.py
│           │   └── schemas.py
│           │
│           ├── routers/
│           │   ├── __init__.py
│           │   ├── admin_router.py
│           │   ├── auth_common.py
│           │   ├── auth_doctor.py
│           │   ├── auth_google.py
│           │   └── auth_user.py
│           │
│           ├── services/
│           │   ├── __init__.py
│           │   ├── account_service.py
│           │   ├── auth_service.py
│           │   ├── doctor_service.py
│           │   └── user_service.py
│           │
│           └── utils/
│               ├── __init__.py
│               ├── db_utils.py
│               ├── email_utils.py
│               ├── jwt_utils.py
│               ├── rate_limiter.py
│               ├── sms_utils.py
│               └── tokens.py
│
└── frontend/
    ├── components/
    │   └── Auth/
    │       ├── DoctorSignupModal.jsx
    │       └── PatientAuthModal.jsx
    │
    ├── contexts/
    │   ├── AuthContext.js
    │   └── AuthPopupContext.js
    │
    ├── navigation/
    │   └── AuthGate.js
    │
    └── utils/
        ├── AuthService.js
        └── AuthHandle.js
```
