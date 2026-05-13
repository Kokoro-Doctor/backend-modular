"""
Chat service - handles chat business logic (message storage, LLM calls, RAG calls).
"""
import json
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any

from boto3.dynamodb.conditions import Key

from fastapi import HTTPException

from app.config import chat_table, OPENAI_API_KEY, RAG_SERVER_URL
from app.logger import get_logger
from app.utils.openai_errors import to_http_exception
from app.utils.rag_errors import is_rag_error_response
from openai import OpenAI
import requests
from requests.exceptions import RequestException

logger = get_logger(__name__)


def store_message(identifier: str, user_message: str, bot_message: str):
    """Store a chat message in DynamoDB"""
    try:
        now = datetime.now(timezone.utc)
        timestamp = int(now.timestamp())
        created_at = now.isoformat()
        date_bucket = now.strftime("%Y-%m-%d")

        chat_table.put_item(
            Item={
                "user_id": identifier,
                "timestamp": timestamp,
                "created_at": created_at,
                "date_bucket": date_bucket,
                "user_message": user_message,
                "bot_message": bot_message,
            }
        )
        logger.info(f"Stored message for identifier: {identifier}")
    except Exception as e:
        logger.error(f"Error storing message: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to store message")


def get_chat_history(identifier: str) -> List[str]:
    """Get chat history for an identifier"""
    try:
        response = chat_table.query(
            KeyConditionExpression="user_id = :id_value",
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

def call_rag_server(message: str, user_id: str, role: str, language: str = "en") -> Optional[str]:
    """
    Attempts to call the RAG server. Returns None on any failure to trigger LLM fallback.
    Also treats RAG responses that look like error messages (e.g. "Error occurred") as failure.
    """
    try:
        payload = {
            "message": message,
            "language": language,
            "user_id": user_id,
            "role": role or "patient",
        }

        logger.info(f"Sending payload to RAG: {json.dumps(payload)}")

        res = requests.post(
            RAG_SERVER_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=60,
        )
        if res.status_code == 200:
            data = res.json().get("response", "").strip()
            if data and data.lower() != "none":
                if is_rag_error_response(data):
                    logger.warning(
                        f"RAG returned error-like response: {data[:80]}... "
                        "Falling back to LLM."
                    )
                    return None
                return data
        else:
            logger.warning(f"RAG server returned non-200 status: {res.status_code}. Falling back to LLM.")
    except RequestException as e:
        logger.warning(f"RAG server request failed: {e}. Falling back to LLM.")
    except Exception as e:
        logger.warning(f"RAG server call failed with unexpected error: {e}. Falling back to LLM.")
    return None


def call_llm_api(history: List[str], user_question: str, language: str = "en") -> str:
    """Call OpenAI LLM API with chat history"""
    context = "\n".join(history) if history else "No prior messages."

    # Multilingual keywords for intent detection
    heart_keywords_en = ["heart", "bp", "blood pressure", "cardio", "cholesterol", "pulse", "ecg", "angina", "palpitation"]
    heart_keywords_hi = ["दिल", "हृदय", "बीपी", "रक्तचाप", "कोलेस्ट्रॉल", "नाड़ी", "ईसीजी", "एनजाइना", "धड़कन"]
    
    reproductive_keywords_en = ["period", "pregnancy", "fertility", "sex", "menstruation", "ovulation", "contraceptive", "hormone"]
    reproductive_keywords_hi = ["पीरियड", "मासिक", "गर्भावस्था", "प्रजनन", "सेक्स", "ओव्यूलेशन", "गर्भनिरोधक", "हार्मोन"]

    q_lower = user_question.lower()
    heart_keywords = heart_keywords_en + heart_keywords_hi
    reproductive_keywords = reproductive_keywords_en + reproductive_keywords_hi
    
    if any(k in q_lower for k in heart_keywords):
        detected_intent = "Heart Health" if language == "en" else "हृदय स्वास्थ्य"
    elif any(k in q_lower for k in reproductive_keywords):
        detected_intent = "Reproductive Health" if language == "en" else "प्रजनन स्वास्थ्य"
    else:
        detected_intent = "General / Other" if language == "en" else "सामान्य / अन्य"

    # Language-specific welcome messages
    welcome_messages = {
        "en": "Hey there! How are you feeling today? I'm your personal health companion — here to support you every step of the way. Would you like help with your heart health or reproductive health today? And remember, this is a safe and private space, so feel free to ask me anything.",
        "hi": "नमस्ते! आज आप कैसा महसूस कर रहे हैं? मैं आपका व्यक्तिगत स्वास्थ्य साथी हूं — हर कदम पर आपकी मदद के लिए यहां हूं। क्या आप आज अपने हृदय स्वास्थ्य या प्रजनन स्वास्थ्य के बारे में मदद चाहेंगे? और याद रखें, यह एक सुरक्षित और निजी स्थान है, इसलिए बेझिझक मुझसे कुछ भी पूछें।"
    }
    
    # Language-specific system messages
    system_messages = {
        "en": "You are a friendly, caring, and empathetic AI health companion developed by Metafied. Your mission is to provide SPECIFIC, ACTIONABLE guidance related to heart health and reproductive health. Always address the user's exact concern with personalized, practical advice. Avoid generic responses. Respond ONLY in English.",
        "hi": "आप Metafied द्वारा विकसित एक मित्रतापूर्ण, देखभाल करने वाला और सहानुभूतिपूर्ण AI स्वास्थ्य साथी हैं। आपका मिशन हृदय स्वास्थ्य और प्रजनन स्वास्थ्य से संबंधित विशिष्ट, व्यावहारिक मार्गदर्शन प्रदान करना है। हमेशा उपयोगकर्ता की सटीक चिंता को व्यक्तिगत, व्यावहारिक सलाह के साथ संबोधित करें। सामान्य प्रतिक्रियाओं से बचें। केवल हिंदी में उत्तर दें।"
    }
    
    welcome_msg = welcome_messages.get(language, welcome_messages["en"])
    system_msg = system_messages.get(language, system_messages["en"])

    prompt = f"""
        You are a friendly, caring, and empathetic AI health companion developed by Metafied.
        Your mission is to provide compassionate and accurate guidance related to **heart health** and **reproductive health**.

        CRITICAL RESPONSE GUIDELINES:
        1. NEVER give generic responses like "seek medical help immediately" or "consult a doctor" unless the situation is life-threatening.
        2. ALWAYS provide SPECIFIC, ACTIONABLE guidance tailored to the user's exact question.
        3. Address the user's specific concern directly - don't give generic advice that could apply to anyone.
        4. Be conversational and personalized - respond as if you're talking to a friend who trusts you.

        Instructions:
        1. Classify the user's query as: Heart Health / Reproductive Health / General.
        2. Respond accordingly:
        
        **Heart Health queries:**
        - Provide SPECIFIC, actionable guidance based on the user's exact concern.
        - Address their specific symptom or question directly.
        - Offer practical lifestyle tips, dietary suggestions, or understanding about their condition.
        - Examples:
          * "Heart pain" → Address potential causes (stress, indigestion, muscle strain), suggest immediate comfort measures (rest, deep breathing), and when to be concerned.
          * "High BP" → Specific dietary changes (reduce sodium, increase potassium), lifestyle modifications (exercise, stress management), and monitoring tips.
          * "Cholesterol" → Specific foods to include/avoid, exercise recommendations, and understanding numbers.
        - AVOID: Generic disclaimers, vague advice, or responses that don't address their specific concern.
        
        **Reproductive Health queries:**
        - Provide supportive, informative, respectful guidance tailored to their specific question.
        - Be specific about their concern (periods, pregnancy, fertility, etc.).
        - Offer practical advice and understanding.
        
        **General queries:**
        - Politely redirect: 
            "That's interesting! I can mainly help with your heart or reproductive health. Would you like me to guide you in one of those areas?"
        
        3. Tone: warm, caring, human-like, conversational - like a trusted friend who knows about health.
        4. IMPORTANT: You MUST respond ONLY in {language.upper()} language. If language is "hi", respond in Hindi (हिंदी). If language is "en", respond in English.
        5. If this is the first message, start with:
        "{welcome_msg}"

        Context from previous messages:
        {context}

        Detected Intent: {detected_intent}

        User's question ({language}): {user_question}

        Now provide a SPECIFIC, ACTIONABLE response that directly addresses their concern. Be personal, warm, and helpful. RESPOND ONLY IN {language.upper()}:

        """

    try:
        if not OPENAI_API_KEY:
            logger.error("OPENAI_API_KEY is not set")
            raise HTTPException(status_code=500, detail="OpenAI API key not configured")

        client = OpenAI(api_key=OPENAI_API_KEY)

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
        )

        return response.choices[0].message.content.strip()
    except Exception as e:
        raise to_http_exception(e, "[CHAT]")


def process_chat_message(
    identifier: str, 
    message: str, 
    language: str = "en", 
    role: str = None,
    is_logged_in: bool = True,
    chat_count: int = None
) -> Dict[str, Any]:
    """
    Process a chat message - tries RAG first, falls back to LLM.
    Always returns the full response.
    
    Args:
        identifier: User/doctor/session identifier
        message: User's message
        language: Language code (default: "en")
        role: User role - "doctor" or "patient" (optional)
        is_logged_in: Whether user is logged in (default: True)
        chat_count: Chat count (ignored - kept for API compatibility)
    
    Returns:
        Dict with response data (always full response)
    """
    history = get_chat_history(identifier)

    # Try RAG first
    logger.info(f"Calling RAG for {identifier} (role: {role}, logged_in: {is_logged_in})")
    
    # FIX: Passing identifier (as user_id) and role
    rag_response = call_rag_server(message, identifier, role, language)
    
    if rag_response:
        logger.info(f"RAG response received for {identifier}")
        full_response = rag_response
    else:
        # Fallback to LLM
        logger.info(f"RAG unavailable or returned no response. Falling back to LLM for {identifier} (role: {role})")
        full_response = call_llm_api(history, message, language)
    
    # Store full message in DynamoDB
    store_message(identifier, message, full_response)
    
    # Always return full response
    return {
        "text": full_response,
        "is_preview": False
    }


# ---------------------------------------------------------------------------
# Chat history query helpers
# ---------------------------------------------------------------------------

def _format_message(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "user_id": item.get("user_id", ""),
        "timestamp": int(item.get("timestamp", 0)),
        "created_at": item.get("created_at"),
        "user_message": item.get("user_message", ""),
        "bot_message": item.get("bot_message", ""),
    }


def fetch_user_history(
    identifier: str,
    date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Query chat history for a single user.

    Uses the table's primary key (user_id / timestamp) — never scans.
    """
    from app.utils import date_to_timestamp_range

    try:
        query_kwargs: Dict[str, Any] = {}

        if date:
            start_ts, end_ts = date_to_timestamp_range(date)
            query_kwargs["KeyConditionExpression"] = (
                Key("user_id").eq(identifier) & Key("timestamp").between(start_ts, end_ts)
            )
        elif start_date and end_date:
            start_ts, _ = date_to_timestamp_range(start_date)
            _, end_ts = date_to_timestamp_range(end_date)
            query_kwargs["KeyConditionExpression"] = (
                Key("user_id").eq(identifier) & Key("timestamp").between(start_ts, end_ts)
            )
        else:
            query_kwargs["KeyConditionExpression"] = Key("user_id").eq(identifier)
            query_kwargs["ScanIndexForward"] = False
            query_kwargs["Limit"] = limit

        messages: List[Dict[str, Any]] = []
        while True:
            response = chat_table.query(**query_kwargs)
            messages.extend(_format_message(item) for item in response.get("Items", []))

            last_key = response.get("LastEvaluatedKey")
            if not last_key or len(messages) >= limit:
                break
            query_kwargs["ExclusiveStartKey"] = last_key

        messages = messages[:limit]
        messages.sort(key=lambda m: m["timestamp"])

        return {"count": len(messages), "messages": messages}

    except Exception as e:
        logger.error(f"Error fetching user history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch user history")


def fetch_global_history(
    date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: Optional[int] = None,
    limit: int = 200,
) -> Dict[str, Any]:
    """Query chat history across ALL users via the date-timestamp-index GSI.

    Computes the set of date_bucket values to query, queries each one, and
    aggregates results up to *limit*.
    """
    from app.utils import date_to_timestamp_range

    GSI_NAME = "date-timestamp-index"

    try:
        buckets: List[str] = []

        if date:
            buckets = [date]
        elif start_date and end_date:
            current = datetime.strptime(start_date, "%Y-%m-%d").date()
            end = datetime.strptime(end_date, "%Y-%m-%d").date()
            while current <= end:
                buckets.append(current.strftime("%Y-%m-%d"))
                current += timedelta(days=1)
        elif days:
            today = datetime.now(timezone.utc).date()
            for i in range(days):
                buckets.append((today - timedelta(days=i)).strftime("%Y-%m-%d"))

        messages: List[Dict[str, Any]] = []

        for bucket in buckets:
            if len(messages) >= limit:
                break

            query_kwargs: Dict[str, Any] = {
                "IndexName": GSI_NAME,
                "KeyConditionExpression": Key("date_bucket").eq(bucket),
                "ScanIndexForward": True,
            }

            while True:
                response = chat_table.query(**query_kwargs)
                messages.extend(_format_message(item) for item in response.get("Items", []))

                last_key = response.get("LastEvaluatedKey")
                if not last_key or len(messages) >= limit:
                    break
                query_kwargs["ExclusiveStartKey"] = last_key

        messages = messages[:limit]
        messages.sort(key=lambda m: m["timestamp"])

        return {"count": len(messages), "messages": messages}

    except Exception as e:
        logger.error(f"Error fetching global history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch global history")