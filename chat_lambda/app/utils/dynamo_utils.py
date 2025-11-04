import json
from datetime import datetime, timezone
from app.config import chat_table
from app.logger import get_logger

logger = get_logger(__name__)

def store_message(identifier, user_message, bot_message):
    try:
        timestamp = int(datetime.now(timezone.utc).timestamp())
        chat_table.put_item(
            Item={
                "email": identifier,
                "timestamp": timestamp,
                "user_message": user_message,
                "bot_message": bot_message,
            }
        )
        logger.info(f"Stored message for identifier: {identifier}")
    except Exception as e:
        logger.error(f"Error storing message: {e}", exc_info=True)


def get_chat_history(identifier):
    try:
        response = chat_table.query(
            KeyConditionExpression="email = :id_value",
            ExpressionAttributeValues={":id_value": identifier},
            ScanIndexForward=False,
            Limit=5,
        )
        items = response.get("Items", [])
        history = []
        for item in reversed(items):
            user_msg = item.get("user_message", "")
            bot_msg = item.get("bot_message", "")
            history.append(f"user: {user_msg}")
            history.append(f"bot: {bot_msg}")
        return history
    except Exception as e:
        logger.error(f"Error fetching chat history: {e}", exc_info=True)
        return []
