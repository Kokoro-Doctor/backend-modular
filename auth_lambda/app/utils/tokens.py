import uuid
from datetime import datetime, timezone, timedelta
from app.logger import get_logger

logger = get_logger(__name__)

def generate_token() -> str:
    t = str(uuid.uuid4())
    logger.debug(f"[tokens] generated token {t}")
    return t

def ttl_minutes_from_now(minutes: int) -> int:
    dt = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    ttl = int(dt.timestamp())
    logger.debug(f"[tokens] ttl (minutes={minutes}) -> {ttl}")
    return ttl

def ttl_days_from_now(days: int) -> int:
    dt = datetime.now(timezone.utc) + timedelta(days=days)
    ttl = int(dt.timestamp())
    logger.debug(f"[tokens] ttl (days={days}) -> {ttl}")
    return ttl

def generate_token_id() -> str:
    """Generate a new UUID for token_id (same as generate_token but semantically clearer)"""
    return generate_token()
