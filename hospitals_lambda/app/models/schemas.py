import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Any, Dict, Optional

from fastapi import File, Form, HTTPException, UploadFile


class HospitalCreate(BaseModel):
    name: str
    password: str
    email: Optional[str] = None
    contact_number: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None

    @model_validator(mode="after")
    def require_email_or_contact(self) -> "HospitalCreate":
        if not self.email and not self.contact_number:
            raise ValueError("At least one of email or contact_number is required")
        return self


class HospitalUpdate(BaseModel):
    hospital_id: str
    name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    contact_number: Optional[str] = None
    email: Optional[str] = None


class HospitalLoginRequest(BaseModel):
    identifier: str = Field(..., description="Hospital email address or contact number")
    password: str


class HospitalIdBody(BaseModel):
    """Body shape for routes that duplicate hospital_id for extra confirmation."""

    hospital_id: str


# --- Staff management schemas ---

class AddPatientRequest(BaseModel):
    doctor_id: Optional[str] = Field(
        None,
        description="Attending doctor; if set, must belong to the hospital in the JWT.",
    )
    phone: str = Field(..., min_length=8, max_length=15, description="Patient phone (E.164 or local digits)")
    name: str

    @field_validator("phone")
    @classmethod
    def phone_digits_only(cls, v: str) -> str:
        digits = re.sub(r"\D", "", v or "")
        if len(digits) < 8 or len(digits) > 13:
            raise ValueError("Phone number must contain 8-13 digits")
        return v
    email: Optional[str] = None
    age: Optional[int] = Field(None, ge=0, le=150, description="Patient age in years")
    gender: Optional[str] = Field(None, max_length=64, description="Patient gender")
    insurer: Optional[str] = Field(None, description="Insurance company name")

    @field_validator("doctor_id", mode="before")
    @classmethod
    def empty_doctor_id_to_none(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class AddPatientForm:
    """multipart/form-data shape for POST /add-patient (documents are now optional).

    Mirrors AddPatientRequest scalar fields plus insurance_policy, hospital_bill,
    prescription file uploads.

    Use: data: AddPatientForm = Depends()
    """

    def __init__(
        self,
        phone: str = Form(..., min_length=8, max_length=15, description="Patient phone (E.164 or local digits)"),
        name: str = Form(...),
        doctor_id: Optional[str] = Form(None, description="Attending doctor; must belong to hospital"),
        email: Optional[str] = Form(None),
        age: Optional[int] = Form(None, ge=0, le=150, description="Patient age in years"),
        gender: Optional[str] = Form(None, max_length=64),
        insurer: Optional[str] = Form(None),
        insurance_policy: Optional[UploadFile] = File(None, description="Insurance policy document (PDF or image)"),
        hospital_bill: Optional[UploadFile] = File(None, description="Hospital bill (PDF or image)"),
        prescription: Optional[UploadFile] = File(None, description="Prescription (PDF or image)"),
    ):
        digits = re.sub(r"\D", "", phone or "")
        if len(digits) < 8 or len(digits) > 13:
            raise HTTPException(status_code=400, detail="Phone number must contain 8-13 digits")
        self.phone = phone
        self.name = name
        self.doctor_id = (doctor_id or "").strip() or None
        self.email = email
        self.age = age
        self.gender = gender
        self.insurer = insurer.strip() if insurer and insurer.strip() else None
        self.insurance_policy = insurance_policy
        self.hospital_bill = hospital_bill
        self.prescription = prescription


class UpdatePatientRequest(BaseModel):
    """Partial patient update from hospital staff.

    Extra JSON keys are accepted and written to the Users row, except protected
    identifiers/ownership fields. Omitted fields are left unchanged.
    """

    model_config = ConfigDict(extra="allow")

    hospital_id: str
    user_id: str = Field(description="Existing patient user_id (required).")
    doctor_id: Optional[str] = Field(
        None,
        description="Doctor to assign; replaces any existing hospital-assigned doctor. Must belong to the hospital in the JWT.",
    )
    name: Optional[str] = None
    email: Optional[str] = None
    age: Optional[int] = Field(None, ge=0, le=150, description="Patient age in years")
    gender: Optional[str] = Field(None, max_length=64, description="Patient gender")
    insurer: Optional[str] = None
    policy_number: Optional[str] = None

    @field_validator("doctor_id", "user_id", mode="before")
    @classmethod
    def empty_identifier_to_none(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class UpdatePatientForm:
    """multipart/form-data equivalent of UpdatePatientRequest plus optional document files.

    Use with FastAPI Depends():
        data: UpdatePatientForm = Depends()
    """

    def __init__(
        self,
        hospital_id: str = Form(..., description="Must match JWT sub"),
        user_id: str = Form(..., description="Existing patient user_id"),
        doctor_id: Optional[str] = Form(None, description="Doctor to assign; replaces current hospital-assigned doctor"),
        name: Optional[str] = Form(None),
        email: Optional[str] = Form(None),
        age: Optional[int] = Form(None, ge=0, le=150, description="Patient age in years"),
        gender: Optional[str] = Form(None, max_length=64),
        insurer: Optional[str] = Form(None),
        policy_number: Optional[str] = Form(None),
        insurance_policy: Optional[UploadFile] = File(None, description="Updated insurance policy (PDF or image, ≤10 MB)"),
        hospital_bill: Optional[UploadFile] = File(None, description="Updated hospital bill (PDF or image, ≤10 MB)"),
        prescription: Optional[UploadFile] = File(None, description="Updated prescription (PDF or image, ≤10 MB)"),
    ):
        self.hospital_id = (hospital_id or "").strip()
        self.user_id = (user_id or "").strip()
        self.doctor_id = (doctor_id or "").strip() or None
        self.name = name
        self.email = email
        self.age = age
        self.gender = gender
        self.insurer = insurer
        self.policy_number = policy_number
        self.insurance_policy = insurance_policy
        self.hospital_bill = hospital_bill
        self.prescription = prescription

    def scalar_updates(self) -> Dict[str, Any]:
        """Return only the scalar (non-file, non-identity) fields that were provided."""
        raw = {
            "name": self.name,
            "email": self.email,
            "age": self.age,
            "gender": self.gender,
            "insurer": self.insurer,
            "policy_number": self.policy_number,
        }
        return {k: v for k, v in raw.items() if v is not None}


class AddDoctorRequest(BaseModel):
    hospital_id: str
    phone: str
    name: str
    specialization: str
    experience: str
    email: Optional[str] = None


class RelationCreateRequest(BaseModel):
    user_id: str
    doctor_id: str
    hospital_id: str
    relation_type: str = Field(default="HOSPITAL_ASSIGNED")
    linked_by: str = Field(default="hospital_staff")

    @field_validator("user_id", "doctor_id", "hospital_id", "relation_type", "linked_by", mode="before")
    @classmethod
    def strip_required_string(cls, v: str) -> str:
        s = str(v or "").strip()
        if not s:
            raise ValueError("field is required")
        return s


class HospitalLoginResponse(BaseModel):
    hospital: Dict[str, Any]
    token: str
    expires_in: int
    message: str


class DiagnosisSummaryResponse(BaseModel):
    """Diagnosis fields persisted on a Users item by insurance autofill."""

    user_id: str
    primary_diagnosis: Optional[str] = None
    primary_icd_code: Optional[str] = None
    additional_diagnosis: Optional[str] = None
    additional_icd_code: Optional[str] = None
    diagnosis_updated_at: Optional[str] = None
