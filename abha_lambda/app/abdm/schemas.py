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


# ---------------------------------------------------------------------------
# Milestone 2 — Data Flow (Section 6)
#
# Inbound webhook payloads (ABDM → Kokoro). consentDetail / keyMaterial are
# kept as free-form dicts on purpose: we persist them verbatim and hand
# keyMaterial straight to the encryption module without re-modelling ABDM's
# evolving shapes.
# ---------------------------------------------------------------------------

class ConsentNotification(BaseModel):
    """The inner `notification` object of the 6.3.1 consent callback."""
    status: Optional[str] = None                 # GRANTED | REVOKED | EXPIRED
    consentId: Optional[str] = None
    consentDetail: Optional[dict] = None          # full consent artefact
    signature: Optional[str] = None
    grantAcknowledgement: Optional[bool] = None

    class Config:
        extra = "allow"


class ConsentNotificationPayload(BaseModel):
    """6.3.1 — ABDM posts this to {callbackURL}/api/v3/consent/request/hip/notify."""
    notification: ConsentNotification

    class Config:
        extra = "allow"


class HiRequest(BaseModel):
    """The inner `hiRequest` object of the 6.3.3 health-information request."""
    consent: Optional[dict] = None                # {"id": "<consentId>"}
    dateRange: Optional[dict] = None              # {"from": ..., "to": ...}
    dataPushUrl: Optional[str] = None             # HIU URL to push encrypted data to
    keyMaterial: Optional[dict] = None            # HIU ECDH public key + nonce

    class Config:
        extra = "allow"


class HealthInformationRequestPayload(BaseModel):
    """6.3.3 — ABDM posts this to {callbackURL}/api/v3/hip/health-information/request."""
    transactionId: Optional[str] = None           # may also arrive as REQUEST-ID header
    hiRequest: HiRequest

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Milestone 3 — HIU (Health Information User)
#
# Inbound API requests (Hospital Portal → Kokoro) and inbound ABDM → Kokoro
# HIU callbacks. Outbound ABDM request bodies are assembled in the service layer.
# ---------------------------------------------------------------------------

# --- Hospital Portal → Kokoro (HIU outbound triggers) ---

class HiuConsentInitRequest(BaseModel):
    """Start a consent request as HIU (4.3.1)."""
    hospital_id: str
    patient_abha_address: str                     # e.g. "abc@sbx"
    hi_types: List[str]                           # consented document types
    date_from: str                                # ISO8601 — permission window start
    date_to: str                                  # ISO8601 — permission window end
    data_erase_at: str                            # ISO8601 — when HIU must erase data
    requester_name: str
    requester_id_value: str                       # e.g. registration number "MH1001"
    requester_id_type: str = "REGNO"
    requester_id_system: str = "https://www.mciindia.org"
    purpose_code: str = "CAREMGT"
    purpose_text: str = "Care Management"
    purpose_ref_uri: str = "www.abdm.gov.in"
    access_mode: str = "VIEW"
    frequency_unit: str = "HOUR"
    frequency_value: int = 1
    frequency_repeats: int = 0
    hip_id: Optional[str] = None                  # optional target HIP service id
    care_contexts: Optional[List[dict]] = None    # optional [{patientReference, careContextReference}]


class HiuConsentStatusRequest(BaseModel):
    hospital_id: str
    consent_request_id: str


class HiuConsentFetchRequest(BaseModel):
    hospital_id: str
    consent_id: str


class HiuHealthInfoRequest(BaseModel):
    hospital_id: str
    consent_id: str
    date_from: str
    date_to: str


# --- ABDM → Kokoro (HIU inbound callbacks) ---

class HiuConsentOnInitPayload(BaseModel):
    """4.3.2 — {callback}/api/v3/hiu/consent/request/on-init."""
    consentRequest: Optional[dict] = None         # {"id": "<consentRequestId>"}
    error: Optional[Any] = None
    response: Optional[Any] = None                 # {"requestId": "<our REQUEST-ID>"}

    class Config:
        extra = "allow"


class HiuConsentNotifyPayload(BaseModel):
    """Patient approved/denied/revoked — {callback}/api/v3/hiu/consent/request/notify."""
    notification: Optional[dict] = None            # {consentRequestId, status, consentArtefacts:[{id}]}

    class Config:
        extra = "allow"


class HiuConsentOnStatusPayload(BaseModel):
    """4.3.6 — {callback}/api/v3/hiu/consent/request/on-status."""
    consentRequest: Optional[dict] = None          # {"id":..., "status":...}
    error: Optional[Any] = None
    response: Optional[Any] = None
    resp: Optional[Any] = None

    class Config:
        extra = "allow"


class HiuConsentOnFetchPayload(BaseModel):
    """4.3.8 — {callback}/api/v3/hiu/consent/on-fetch."""
    consent: Optional[dict] = None                 # {"status":..., "consentDetail":{...}, "signature":...}
    error: Optional[Any] = None
    response: Optional[Any] = None
    resp: Optional[Any] = None

    class Config:
        extra = "allow"


class HiuDataTransferPayload(BaseModel):
    """6.3.5 inbound — a HIP pushes encrypted records to our HIU dataPushUrl."""
    pageNumber: Optional[int] = None
    pageCount: Optional[int] = None
    transactionId: Optional[str] = None
    entries: Optional[List[dict]] = None
    keyMaterial: Optional[dict] = None             # the HIP's ECDH public key + nonce

    class Config:
        extra = "allow"
