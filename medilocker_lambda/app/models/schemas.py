from pydantic import BaseModel
from typing import List, Dict, Optional

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

class PrescriptionRequest(BaseModel):
    user_id: str
    filenames: Optional[List[str]] = None  # If None, use all files
    patient_symptoms: Optional[str] = None  # Optional additional context
