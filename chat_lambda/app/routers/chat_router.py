from fastapi import APIRouter, HTTPException
from app.models.schemas import ChatRequest
from app.utils.dynamo_utils import store_message, get_chat_history
from app.utils.llm_utils import call_llm_api
from app.utils.rag_utils import call_rag_server
from app.logger import logger

router = APIRouter()

@router.post("/chat")
async def chat(request: ChatRequest):
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message is required")

    identifier = request.user_id or request.session_id or request.doctor_id
    if not identifier:
        raise HTTPException(status_code=400, detail="Either user_id, session_id, or doctor_id required")

    history = get_chat_history(identifier)

    # Try RAG first
    logger.info(f"Calling RAG for {identifier}")
    rag_response = call_rag_server(request.message, request.language)
    if rag_response:
        logger.info(f"RAG response received for {identifier}")
        store_message(identifier, request.message, rag_response)
        return {"text": rag_response}

    # Fallback to LLM
    logger.info(f"RAG unavailable or returned no response. Falling back to LLM for {identifier}")
    ai_response = call_llm_api(history, request.message, request.language)
    store_message(identifier, request.message, ai_response)
    return {"text": ai_response}
