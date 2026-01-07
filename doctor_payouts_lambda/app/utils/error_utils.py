from fastapi import HTTPException
from app.logger import get_logger

logger = get_logger(__name__)


def handle_exception(e: Exception, operation: str):
    """
    Handle exceptions and convert to HTTPException with proper logging.
    """
    if isinstance(e, HTTPException):
        raise e
    
    logger.error(f"Error in {operation}: {str(e)}", exc_info=True)
    raise HTTPException(status_code=500, detail=f"Failed to {operation.lower()}: {str(e)}")

