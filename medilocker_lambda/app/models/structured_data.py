from pydantic import BaseModel, Field
from typing import List, Optional, Dict


class DocumentMetadata(BaseModel):
    document_date: Optional[str] = None
    doctor_name: Optional[str] = None
    hospital_name: Optional[str] = None
    department: Optional[str] = None


class PatientDetails(BaseModel):
    name: Optional[str] = None
    age: Optional[str] = None
    gender: Optional[str] = None


class StructuredMedicalData(BaseModel):
    document_category: Optional[str] = "OTHER"

    document_metadata: DocumentMetadata = DocumentMetadata()
    patient_details: PatientDetails = PatientDetails()

    diagnoses: List[str] = Field(default_factory=list)
    symptoms: List[str] = Field(default_factory=list)
    medical_conditions: List[str] = Field(default_factory=list)
    medications: List[Dict] = Field(default_factory=list)
    tests: List[str] = Field(default_factory=list)
    lab_values: List[Dict] = Field(default_factory=list)
    medical_history: List[str] = Field(default_factory=list)
    clinical_context: List[str] = Field(default_factory=list)

    document_summary: Optional[str] = ""
