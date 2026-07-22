from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field, model_validator


class GoogleAuthRequest(BaseModel):
    token: str  # ID token


class SignupOtpRequest(BaseModel):
    phoneNumber: str = Field(..., min_length=8)
    email: EmailStr = Field(...)


class LoginOtpRequest(BaseModel):
    identifier: str = Field(...)  # Can be email or phone number
    preferredChannel: Optional[Literal["email", "sms"]] = Field(default="email")  # Default to email


class SignupOtpVerify(BaseModel):
    phoneNumber: str = Field(..., min_length=8)
    otp: str = Field(..., min_length=4, max_length=6)
    role: Literal["user", "doctor"]


class LoginRequest(BaseModel):
    identifier: str = Field(...)  # Can be email or phone number
    otp: Optional[str] = Field(default=None, min_length=4, max_length=6)


class UserProfileCreate(BaseModel):
    phoneNumber: str = Field(..., min_length=8)
    # email: EmailStr = Field(...)  # Now mandatory
    # otp: str = Field(..., min_length=4, max_length=6)
    # name: str = Field(..., min_length=2)
    email: Optional[EmailStr] = None  # Optional for experimental flow
    otp: Optional[str] = Field(default=None, min_length=4, max_length=6)
    name: Optional[str] = None


class AbhaSignupRequest(BaseModel):
    """Provision a loginable Kokoro user from an existing ABHA account."""
    abha_number: str = Field(...)
    hospital_id: Optional[str] = None  # optional: ABHA onboarding may run without a hospital context


class DoctorProfileCreate(BaseModel):
    phoneNumber: str = Field(..., min_length=8)
    # email: EmailStr = Field(...)  # Now mandatory (same as user signup)
    # otp: str = Field(..., min_length=4, max_length=6)
    # name: str = Field(..., min_length=2)
    email: Optional[EmailStr] = None  # Optional for experimental flow
    otp: Optional[str] = Field(default=None, min_length=4, max_length=6)
    name: Optional[str] = None  # Optional for experimental flow
    specialization: Optional[str] = None
    experience: Optional[int] = Field(default=None, ge=0, le=80)


class DeleteAccountRequest(BaseModel):
    phoneNumber: Optional[str] = Field(default=None, min_length=8)
    hospital_id: Optional[str] = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def require_deletion_target(self) -> "DeleteAccountRequest":
        if not (self.phoneNumber or "").strip() and not (self.hospital_id or "").strip():
            raise ValueError("phoneNumber or hospital_id is required")
        return self
