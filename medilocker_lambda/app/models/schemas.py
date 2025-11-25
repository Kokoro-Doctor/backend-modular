from pydantic import BaseModel
from typing import List, Dict, Optional

class FileUploadModel(BaseModel):
    filename: str
    content: str
    metadata: Optional[Dict[str, str]] = {}

class UploadRequest(BaseModel):
    email: str
    files: List[FileUploadModel]

class EmailRequest(BaseModel):
    email: str

class FileRequest(BaseModel):
    email: str
    filename: str

class PrescriptionRequest(BaseModel):
    email: str
    filenames: Optional[List[str]] = None  # If None, use all files
    patient_symptoms: Optional[str] = None  # Optional additional context
