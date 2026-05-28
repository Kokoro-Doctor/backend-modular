"""
Pydantic models mirroring ABDM API response shapes.
"""
from typing import Any, List, Optional
from pydantic import BaseModel


class ABDMTokens(BaseModel):
    token: str
    expiresIn: int
    refreshToken: str
    refreshExpiresIn: int


class ABHAProfile(BaseModel):
    ABHANumber: Optional[str] = None
    preferredAddress: Optional[str] = None        # enrollment response field
    preferredAbhaAddress: Optional[str] = None    # profile/account field
    firstName: Optional[str] = None
    middleName: Optional[str] = None
    lastName: Optional[str] = None
    name: Optional[str] = None
    dob: Optional[str] = None
    yearOfBirth: Optional[str] = None
    dayOfBirth: Optional[str] = None
    monthOfBirth: Optional[str] = None
    gender: Optional[str] = None
    photo: Optional[str] = None
    profilePhoto: Optional[str] = None
    mobile: Optional[str] = None
    mobileVerified: Optional[bool] = None
    email: Optional[str] = None
    phrAddress: Optional[List[str]] = None
    address: Optional[str] = None
    districtCode: Optional[str] = None
    stateCode: Optional[str] = None
    pinCode: Optional[str] = None
    pincode: Optional[str] = None
    stateName: Optional[str] = None
    districtName: Optional[str] = None
    abhaStatus: Optional[str] = None
    status: Optional[str] = None
    authMethods: Optional[List[str]] = None
    verificationStatus: Optional[str] = None
    verificationType: Optional[str] = None
    kycVerified: Optional[bool] = None
    createdDate: Optional[str] = None

    class Config:
        extra = "allow"


class EnrollmentOTPResponse(BaseModel):
    txnId: str
    message: str


class EnrollmentResponse(BaseModel):
    message: str
    txnId: str
    tokens: ABDMTokens
    ABHAProfile: ABHAProfile
    isNew: bool


class LoginOTPResponse(BaseModel):
    txnId: str
    message: str


class LoginVerifyResponse(BaseModel):
    tokens: ABDMTokens
    ABHAProfile: Optional[ABHAProfile] = None
    message: Optional[str] = None

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Milestone 2 — HIP Initiated Linking
# ---------------------------------------------------------------------------

class CareContext(BaseModel):
    referenceNumber: str
    display: str


class CareContextPatient(BaseModel):
    referenceNumber: str
    display: str
    careContexts: List[CareContext]
    hiType: str  # PRESCRIPTION | DiagnosticReport | OPConsultation | etc.
    count: int


# Inbound webhook payloads (ABDM → Kokoro)

class LinkTokenCallbackPayload(BaseModel):
    """4.3.2 — ABDM posts this to our /api/v3/hip/token/on-generate-token endpoint."""
    abhaAddress: str
    linkToken: str
    response: Optional[Any] = None

    class Config:
        extra = "allow"


class CareContextCallbackPayload(BaseModel):
    """4.3.4 — ABDM posts this to our /api/v3/link/on_carecontext endpoint."""
    abhaAddress: Optional[str] = None
    status: Optional[str] = None
    response: Optional[Any] = None
    error: Optional[Any] = None

    class Config:
        extra = "allow"
