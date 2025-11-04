from pydantic import BaseModel
from typing import Optional

class ChatRequest(BaseModel):
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    message: str
    language: str = "en"
