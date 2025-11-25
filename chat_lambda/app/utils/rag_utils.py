import requests
from requests.exceptions import RequestException
from app.config import RAG_SERVER_URL
from app.logger import get_logger

logger = get_logger(__name__)

def call_rag_server(message, language):
    """
    Attempts to call the RAG server. Returns None on any failure to trigger LLM fallback.
    """
    try:
        payload = {"message": message, "language": language}
        res = requests.post(
            RAG_SERVER_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=60
        )
        if res.status_code == 200:
            data = res.json().get("response", "").strip()
            if data and data.lower() != "none":
                return data
        else:
            logger.warning(f"RAG server returned non-200 status: {res.status_code}. Falling back to LLM.")
    except RequestException as e:
        # Catches all requests exceptions including Timeout, ConnectionError, ConnectTimeoutError, etc.
        logger.warning(f"RAG server request failed: {e}. Falling back to LLM.")
    except Exception as e:
        # Catch any other unexpected errors (e.g., JSON parsing errors)
        logger.warning(f"RAG server call failed with unexpected error: {e}. Falling back to LLM.")
    return None
