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
