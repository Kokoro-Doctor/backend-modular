import requests
from app import config
from app.logger import get_logger

logger = get_logger(__name__)

def send_otp_sms(phone_number: str, otp: str):
    url = "https://www.fast2sms.com/dev/bulkV2"
    headers = {"authorization": config.FAST2SMS_API_KEY}
    payload = {
        "variables_values": otp,
        "route": "q",
        "numbers": phone_number
    }

    try:
        response = requests.post(url, data=payload, headers=headers, timeout=10)
        response.raise_for_status()
        logger.info(f"[sms_utils] OTP sent to {phone_number} response={response.json()}")
        return response.json()
    except requests.RequestException:
        logger.exception("[sms_utils] Failed to send OTP SMS")
        raise
