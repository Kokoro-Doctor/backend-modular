from pydantic import BaseModel
from typing import List, Dict, Optional, Any

class FileUploadModel(BaseModel):
    filename: str
    content: str
    metadata: Optional[Dict[str, str]] = {}

class UploadRequest(BaseModel):
    user_id: str
    files: List[FileUploadModel]

class UserRequest(BaseModel):
    user_id: str

class FileRequest(BaseModel):
    user_id: str
    filename: str

class ExtractionRequest(BaseModel):
    files: List[FileUploadModel]  # Files with base64 content
    frontend_patient_details: Optional[Dict[str, Any]] = None  # Optional patient details from frontend


class ClinicalQueryRequest(BaseModel):
    question: str = ""


class SavePrescriptionRequest(BaseModel):
    """Request body for saving an approved prescription to Medilocker."""
    prescription_pdf: str  # Base64-encoded PDF content



class InsuranceAnalyzeFiles(BaseModel):
    """Document types for multi-file claim analysis."""
    # Used only for documentation — actual upload goes via multipart form
    claim_form: str  # required
    hospital_bill: Optional[str] = None
    doctor_prescription: Optional[str] = None
    insurance_savings_breakdown: Optional[str] = None
