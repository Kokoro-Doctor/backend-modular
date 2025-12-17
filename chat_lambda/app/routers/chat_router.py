"""
Chat router - thin wrapper around chat service.
"""
from fastapi import APIRouter, HTTPException
from app.models.schemas import ChatRequest
from app.services.chat_service import process_chat_message
from app.logger import logger

router = APIRouter()


@router.post("/chat")
async def chat(request: ChatRequest):
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message is required")

    identifier = request.user_id or request.session_id or request.doctor_id
    if not identifier:
        raise HTTPException(status_code=400, detail="Either user_id, session_id, or doctor_id required")

    response_text = process_chat_message(identifier, request.message, request.language)
    return {"text": response_text}
