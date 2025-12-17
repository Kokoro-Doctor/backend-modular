"""
Chat service - handles chat business logic (message storage, LLM calls, RAG calls).
"""
import json
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import HTTPException

from app.config import chat_table, OPENAI_API_KEY, RAG_SERVER_URL
from app.logger import get_logger
from openai import OpenAI
import requests
from requests.exceptions import RequestException

logger = get_logger(__name__)


def store_message(identifier: str, user_message: str, bot_message: str):
    """Store a chat message in DynamoDB"""
    try:
        timestamp = int(datetime.now(timezone.utc).timestamp())
        chat_table.put_item(
            Item={
                "user_id": identifier,
                "timestamp": timestamp,
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


def call_rag_server(message: str, language: str = "en") -> Optional[str]:
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
        logger.warning(f"RAG server request failed: {e}. Falling back to LLM.")
    except Exception as e:
        logger.warning(f"RAG server call failed with unexpected error: {e}. Falling back to LLM.")
    return None


def call_llm_api(history: List[str], user_question: str, language: str = "en") -> str:
    """Call OpenAI LLM API with chat history"""
    context = "\n".join(history) if history else "No prior messages."

    heart_keywords = ["heart", "bp", "blood pressure", "cardio", "cholesterol", "pulse", "ecg", "angina", "palpitation"]
    reproductive_keywords = ["period", "pregnancy", "fertility", "sex", "menstruation", "ovulation", "contraceptive", "hormone"]

    q_lower = user_question.lower()
    if any(k in q_lower for k in heart_keywords):
        detected_intent = "Heart Health"
    elif any(k in q_lower for k in reproductive_keywords):
        detected_intent = "Reproductive Health"
    else:
        detected_intent = "General / Other"

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
        4. Languages: Respond in {language} (English, Hindi, Spanish, or Telugu).
        5. If this is the first message, start with:
        "Hey there! How are you feeling today? I'm your personal health companion — here to support you every step of the way.
        Would you like help with your heart health or reproductive health today?
        And remember, this is a safe and private space, so feel free to ask me anything."

        Context from previous messages:
        {context}

        Detected Intent: {detected_intent}

        User's question ({language}): {user_question}

        Now provide a SPECIFIC, ACTIONABLE response that directly addresses their concern. Be personal, warm, and helpful:

        """

    try:
        if not OPENAI_API_KEY:
            logger.error("OPENAI_API_KEY is not set")
            raise HTTPException(status_code=500, detail="OpenAI API key not configured")
        
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a friendly, caring, and empathetic AI health companion developed by Metafied. Your mission is to provide SPECIFIC, ACTIONABLE guidance related to heart health and reproductive health. Always address the user's exact concern with personalized, practical advice. Avoid generic responses."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
        )
        
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"OpenAI API error: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="LLM request failed")


def process_chat_message(identifier: str, message: str, language: str = "en") -> str:
    """
    Process a chat message - tries RAG first, falls back to LLM.
    Returns the bot response and stores the conversation.
    """
    history = get_chat_history(identifier)

    # Try RAG first
    logger.info(f"Calling RAG for {identifier}")
    rag_response = call_rag_server(message, language)
    if rag_response:
        logger.info(f"RAG response received for {identifier}")
        store_message(identifier, message, rag_response)
        return rag_response

    # Fallback to LLM
    logger.info(f"RAG unavailable or returned no response. Falling back to LLM for {identifier}")
    ai_response = call_llm_api(history, message, language)
    store_message(identifier, message, ai_response)
    return ai_response

