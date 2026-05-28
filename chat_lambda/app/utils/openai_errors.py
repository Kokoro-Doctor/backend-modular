"""
OpenAI API error handling - converts API exceptions to HTTPException.
Use in service layer so routers stay thin.
"""
from fastapi import HTTPException
from openai import RateLimitError, AuthenticationError, APIError

from app.logger import get_logger

logger = get_logger(__name__)


def to_http_exception(e: Exception, log_prefix: str = "") -> HTTPException:
    """
    Convert OpenAI/API exception to HTTPException.
    Caller should: raise to_http_exception(e, "[PREFIX]")

    Re-raises HTTPException as-is (e.g. for missing API key).
    """
    if isinstance(e, HTTPException):
        raise e
    if isinstance(e, RateLimitError):
        return HTTPException(
            status_code=429,
            detail="AI service quota exceeded. Please check your plan and billing.",
        )
    if isinstance(e, AuthenticationError):
        return HTTPException(
            status_code=503,
            detail="AI service authentication failed. Please check configuration.",
        )
    if isinstance(e, APIError):
        logger.error(f"{log_prefix} OpenAI API error: {type(e).__name__}: {e}")
        return HTTPException(
            status_code=503,
            detail="AI service temporarily unavailable. Please try again later.",
        )
    logger.exception(f"{log_prefix} Unexpected error: {type(e).__name__}: {e}")
    return HTTPException(
        status_code=503,
        detail="AI service temporarily unavailable. Please try again later.",
    )
