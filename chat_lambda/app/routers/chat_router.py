"""
Chat router - thin wrapper around chat service.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from app.models.schemas import ChatRequest, ChatResponse, ChatHistoryResponse
from app.services.chat_service import (
    process_chat_message,
    fetch_user_history,
    fetch_global_history,
)
from app.logger import logger

router = APIRouter()


@router.post("/chat/send", response_model=ChatResponse)
async def chat(request: ChatRequest):
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message is required")

    identifier = request.user_id or request.session_id or request.doctor_id
    if not identifier:
        raise HTTPException(status_code=400, detail="Either user_id, session_id, or doctor_id required")

    # Detect login status: user is logged in if user_id or doctor_id exists
    is_logged_in = bool(request.user_id or request.doctor_id)

    response = process_chat_message(
        identifier, 
        request.message, 
        request.language, 
        request.role,
        is_logged_in,
        request.chat_count
    )
    
    return response


@router.get("/chat/history/user", response_model=ChatHistoryResponse)
async def user_history(
    identifier: str = Query(..., description="User / session / doctor identifier"),
    date: Optional[str] = Query(None, description="Single date (YYYY-MM-DD)"),
    start_date: Optional[str] = Query(None, description="Range start (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Range end (YYYY-MM-DD)"),
    limit: int = Query(50, ge=1, le=500),
):
    has_date = date is not None
    has_range = start_date is not None or end_date is not None

    if has_date and has_range:
        raise HTTPException(
            status_code=400,
            detail="Provide either 'date' or a date range (start_date + end_date), not both.",
        )

    if (start_date is None) != (end_date is None):
        raise HTTPException(
            status_code=400,
            detail="Both start_date and end_date are required for a date range.",
        )

    return fetch_user_history(
        identifier=identifier,
        date=date,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )


@router.get("/chat/history/global", response_model=ChatHistoryResponse)
async def global_history(
    days: Optional[int] = Query(None, ge=1, le=365, description="Last N days"),
    date: Optional[str] = Query(None, description="Single date (YYYY-MM-DD)"),
    start_date: Optional[str] = Query(None, description="Range start (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Range end (YYYY-MM-DD)"),
    limit: int = Query(200, ge=1, le=5000),
):
    modes = sum([
        date is not None,
        start_date is not None or end_date is not None,
        days is not None,
    ])

    if modes == 0:
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one mode: 'date', a date range (start_date + end_date), or 'days'.",
        )
    if modes > 1:
        raise HTTPException(
            status_code=400,
            detail="Provide only one mode: 'date', a date range (start_date + end_date), or 'days'.",
        )

    if (start_date is None) != (end_date is None):
        raise HTTPException(
            status_code=400,
            detail="Both start_date and end_date are required for a date range.",
        )

    return fetch_global_history(
        date=date,
        start_date=start_date,
        end_date=end_date,
        days=days,
        limit=limit,
    )
