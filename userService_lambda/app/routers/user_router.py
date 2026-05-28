"""
User router - handles user profile endpoints.
"""
from fastapi import APIRouter, HTTPException

from app.logger import get_logger
from app.services.user_service import (
    get_user_by_id,
    build_user_payload,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/{user_id}")
def get_user(user_id: str):
    """
    Get a single user by user_id.
    
    This is the primary endpoint for fetching user profiles.
    Authentication can be handled via API Gateway authorizers if needed.
    """
    try:
        user = get_user_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        profile = build_user_payload(user)
        logger.info("[GetUser] Retrieved user %s", user_id)
        return {"user": profile}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[GetUser] Unexpected error")
        raise HTTPException(status_code=500, detail=str(exc))

