import bcrypt
from typing import Any
from . import __name__  # avoid flake warnings - not used
from app.logger import get_logger

logger = get_logger(__name__)

def hash_password(password: str) -> str:
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    logger.debug("[security] password hashed")
    return hashed

def verify_password(plain: str, hashed: str) -> bool:
    try:
        result = bcrypt.checkpw(plain.encode(), hashed.encode())
        logger.debug(f"[security] password verify result: {result}")
        return result
    except Exception as e:
        logger.exception("[security] verify_password error")
        return False
