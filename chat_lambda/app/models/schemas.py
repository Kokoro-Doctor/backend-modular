from pydantic import BaseModel
from typing import Optional, Dict, Any, List

class ChatRequest(BaseModel):
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    doctor_id: Optional[str] = None
    message: str
    language: str = "en"
    role: Optional[str] = "patient" # "doctor" or "patient"
    chat_count: Optional[int] = None  # Current chat count for anonymous users

class ChatResponse(BaseModel):
    """Response model for chat endpoint - always returns full response"""
    text: Optional[str] = None  # Full response text
    is_preview: bool = False  # Always False - kept for API compatibility


class ChatHistoryMessage(BaseModel):
    user_id: str
    timestamp: int
    created_at: Optional[str] = None
    user_message: str
    bot_message: str


class ChatHistoryResponse(BaseModel):
    count: int
    messages: List[ChatHistoryMessage]
