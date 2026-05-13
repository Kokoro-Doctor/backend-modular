from fastapi import HTTPException
from app.logger import get_logger

logger = get_logger(__name__)


def handle_exception(e: Exception, context: str = "Operation"):
    """Centralized error handler for consistent logging and HTTPException raising."""
    if isinstance(e, HTTPException):
        raise e
    logger.exception(f"{context} failed: {e}")
    raise HTTPException(status_code=500, detail=str(e))
