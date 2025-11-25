from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field


class GoogleAuthRequest(BaseModel):
    token: str  # ID token


class SignupOtpRequest(BaseModel):
    phoneNumber: str = Field(..., min_length=8)


class LoginOtpRequest(BaseModel):
    phoneNumber: str = Field(..., min_length=8)


class SignupOtpVerify(BaseModel):
    phoneNumber: str = Field(..., min_length=8)
    otp: str = Field(..., min_length=4, max_length=6)
    role: Literal["user", "doctor"]


class LoginRequest(BaseModel):
    phoneNumber: str = Field(..., min_length=8)
    password: Optional[str] = Field(default=None, min_length=6)
    otp: Optional[str] = Field(default=None, min_length=4, max_length=6)


class UserProfileCreate(BaseModel):
    phoneNumber: str = Field(..., min_length=8)
    otp: str = Field(..., min_length=4, max_length=6)
    name: str = Field(..., min_length=2)
    password: str = Field(..., min_length=6)
    email: Optional[EmailStr] = None


class DoctorProfileCreate(BaseModel):
    phoneNumber: str = Field(..., min_length=8)
    otp: str = Field(..., min_length=4, max_length=6)
    name: str = Field(..., min_length=2)
    password: str = Field(..., min_length=6)
    specialization: Optional[str] = None
    experience: Optional[int] = Field(default=None, ge=0, le=80)
    email: Optional[EmailStr] = None
