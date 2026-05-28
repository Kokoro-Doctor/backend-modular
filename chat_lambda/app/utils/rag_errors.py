"""
RAG response error detection - treats error-like responses as failure for LLM fallback.
Use in service layer, same pattern as openai_errors.
"""

# RAG responses that indicate an error - treat as failure and fall back to LLM
RAG_ERROR_PHRASES = (
    "error occurred",
    "an error occurred",
    "something went wrong",
    "failed to",
)


def is_rag_error_response(text: str) -> bool:
    """
    Check if RAG response looks like an error message rather than a real answer.
    Returns True if the response should be treated as failure (trigger LLM fallback).
    """
    if not text or not text.strip():
        return True
    lower = text.strip().lower()
    return any(phrase in lower for phrase in RAG_ERROR_PHRASES)
