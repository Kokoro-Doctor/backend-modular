from fastapi import HTTPException
from app.logger import get_logger

logger = get_logger(__name__)

def handle_exception(e, context: str):
    logger.exception(f"{context} failed: {e}")
    raise HTTPException(500, detail=str(e))
