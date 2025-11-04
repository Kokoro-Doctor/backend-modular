import requests
from app.config import RAG_SERVER_URL
from app.logger import get_logger

logger = get_logger(__name__)

def call_rag_server(message, language):
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
    except Exception as e:
        logger.warning(f"RAG server call failed: {e}")
    return None
