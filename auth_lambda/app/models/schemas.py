from pydantic import BaseModel, EmailStr, Field
from typing import Optional

# Auth / request schemas

class GoogleAuthRequest(BaseModel):
    token: str  # ID token

class UserSignup(BaseModel):
    username: str = Field(..., min_length=2)
    email: EmailStr
    password: str = Field(..., min_length=3, max_length=25)
    phoneNumber: Optional[str] = None
    location: Optional[str] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class DoctorSignup(BaseModel):
    doctorname: str = Field(..., min_length=2)
    email: EmailStr
    password: str = Field(..., min_length=5, max_length=25)
    phoneNumber: Optional[str] = None
    location: Optional[str] = None

class DoctorLogin(BaseModel):
    email: EmailStr
    password: str

class PasswordResetRequest(BaseModel):
    email: EmailStr

class PasswordResetConfirm(BaseModel):
    email: EmailStr
    token: str
    new_password: str

class MobileOTPRequest(BaseModel):
    email: str
    phoneNumber: str

class MobileOTPVerify(BaseModel):
    email: str
    phoneNumber: str
    otp: str
