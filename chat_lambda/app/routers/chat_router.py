from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.models.schemas import ChatRequest
from app.utils.dynamo_utils import store_message, get_chat_history
from app.utils.llm_utils import call_llm_api, call_llm_api_stream
from app.utils.rag_utils import call_rag_server
from app.logger import logger

router = APIRouter()

@router.post("/chat")
async def chat(request: ChatRequest):
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message is required")

    identifier = request.user_id or request.session_id
    if not identifier:
        raise HTTPException(status_code=400, detail="Either user_id or session_id required")

    history = get_chat_history(identifier)

    # Try RAG first
    logger.info(f"Calling RAG for {identifier}")
    rag_response = call_rag_server(request.message, request.language)
    if rag_response:
        store_message(identifier, request.message, rag_response)
        return {"text": rag_response}

    # Fallback to LLM
    logger.info(f"Calling LLM for {identifier}")
    ai_response = call_llm_api(history, request.message, request.language)
    store_message(identifier, request.message, ai_response)
    return {"text": ai_response}


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message is required")

    identifier = request.user_id or request.session_id
    if not identifier:
        raise HTTPException(status_code=400, detail="Either user_id or session_id required")

    history = get_chat_history(identifier)

    logger.info(f"Streaming chat initiated for {identifier}")

    rag_response = call_rag_server(request.message, request.language)
    if rag_response:
        logger.info(f"Streaming RAG response for {identifier}")

        def rag_generator():
            yield rag_response

        def persist_rag():
            store_message(identifier, request.message, rag_response)

        return StreamingResponse(
            rag_generator(),
            media_type="text/plain; charset=utf-8",
            background=BackgroundTask(persist_rag),
        )

    logger.info(f"Streaming LLM fallback for {identifier}")
    llm_stream = call_llm_api_stream(history, request.message, request.language)
    final_response = {"text": ""}

    def stream_generator():
        for token in llm_stream:
            final_response["text"] += token
            yield token

    def persist_llm():
        store_message(identifier, request.message, final_response["text"])

    return StreamingResponse(
        stream_generator(),
        media_type="text/plain; charset=utf-8",
        background=BackgroundTask(persist_llm),
    )
