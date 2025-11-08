from openai import OpenAI
from fastapi import HTTPException
from app.config import OPENAI_API_KEY
from app.logger import logger

def call_llm_api(history, user_question, language="en"):
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

    # --- Prompt ---
    prompt = f"""
        You are a friendly, caring, and empathetic AI health companion developed by Metafied.
        Your mission is to provide compassionate and accurate guidance related to **heart health** and **reproductive health**.

        Instructions:
        1. Classify the user's query as: Heart Health / Reproductive Health / General.
        2. Respond accordingly:
        - Heart Health → concise, actionable guidance (1-2 sentences) on lifestyle, diet, or medications.
        - Reproductive Health → supportive, informative, respectful guidance.
        - General → politely redirect: 
            "That's interesting! I can mainly help with your heart or reproductive health. Would you like me to guide you in one of those areas?"
        3. Tone: warm, caring, human-like.
        4. Languages: English, Hindi, Spanish, Telugu.
        5. If this is the first message, start with:
        "Hey there! How are you feeling today? I'm your personal health companion — here to support you every step of the way.
        Would you like help with your heart health or reproductive health today?
        And remember, this is a safe and private space, so feel free to ask me anything."

        Context:
        {context}

        Detected Intent: {detected_intent}

        User ({language}): {user_question}

        AI Response ({language}):
        """


    try:
        if not OPENAI_API_KEY:
            logger.error("OPENAI_API_KEY is not set")
            raise HTTPException(status_code=500, detail="OpenAI API key not configured")
        
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a friendly, caring, and empathetic AI health companion developed by Metafied. Your mission is to provide compassionate and accurate guidance related to heart health and reproductive health."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
        )
        
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"OpenAI API error: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="LLM request failed")
