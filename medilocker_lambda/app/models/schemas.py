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



# Hospital raw data ingestion
class HospitalUploadRequest(BaseModel):
    """Request for direct API upload of hospital raw file."""
    hospital_id: str
    patient_id: str
    filename: str
    content: str  # Base64-encoded file content


class HospitalPresignFileItem(BaseModel):
    """Single file item for batch presign request."""
    filename: str


class HospitalPresignRequest(BaseModel):
    """Request for presigned S3 upload URLs (supports multiple files)."""
    hospital_id: str
    patient_id: str
    files: List[HospitalPresignFileItem]


class HospitalConfirmFileItem(BaseModel):
    """Single file item for batch confirm request."""
    file_id: str
    filename: str
    file_size: Optional[int] = None


class HospitalConfirmUploadRequest(BaseModel):
    """Request to confirm presigned uploads completed (save metadata for multiple files)."""
    hospital_id: str
    patient_id: str
    files: List[HospitalConfirmFileItem]

class InsuranceAnalyzeFiles(BaseModel):
    """Document types for multi-file claim analysis."""
    # Used only for documentation — actual upload goes via multipart form
    claim_form: str  # required
    hospital_bill: Optional[str] = None
    doctor_prescription: Optional[str] = None
    insurance_savings_breakdown: Optional[str] = None
