import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app import config
from app.logger import get_logger

logger = get_logger(__name__)

_sns_client = boto3.client("sns", region_name=config.SMS_AWS_REGION)


def _format_phone_number(raw_phone: str) -> str:
    digits = raw_phone.strip()

    if digits.startswith("+"):
        return digits

    digits = digits.lstrip("0").replace(" ", "")
    return f"{config.SMS_COUNTRY_CODE}{digits}"


def send_sms(phone_number: str, message: str, sms_type: str = "Transactional"):
    formatted_phone = _format_phone_number(phone_number)
    logger.info(f"[sms_utils] Attempting to send SMS to {formatted_phone} (original: {phone_number})")

    try:
        response = _sns_client.publish(
            PhoneNumber=formatted_phone,
            Message=message,
            MessageAttributes={
                "AWS.SNS.SMS.SMSType": {
                    "DataType": "String",
                    "StringValue": sms_type,
                }
            },
        )
        
        message_id = response.get("MessageId")
        if not message_id:
            logger.error(f"[sms_utils] SNS publish succeeded but no MessageId returned. Response: {response}")
            raise Exception("SMS send failed: No MessageId returned from SNS")
        
        logger.info(
            "[sms_utils] SMS sent successfully to %s messageId=%s",
            formatted_phone,
            message_id,
        )
        logger.debug("[sms_utils] SNS response: %s", response)
        return response
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_message = e.response.get("Error", {}).get("Message", str(e))
        logger.error(
            f"[sms_utils] AWS SNS ClientError sending SMS to {formatted_phone}: "
            f"Code={error_code}, Message={error_message}"
        )
        raise
    except BotoCoreError as e:
        logger.error(f"[sms_utils] BotoCoreError sending SMS to {formatted_phone}: {e}")
        raise
    except Exception as e:
        logger.error(f"[sms_utils] Unexpected error sending SMS to {formatted_phone}: {e}")
        raise


def send_otp_sms(phone_number: str, otp: str):
    """Send OTP via SMS. Raises exception if SMS sending fails."""
    message = (
        f"Your Kokoro.Doctor OTP is {otp}. "
        "It is valid for 5 minutes. Please do not share it with anyone."
    )
    logger.info(f"[send_otp_sms] Sending OTP to {phone_number}")
    try:
        result = send_sms(phone_number, message)
        logger.info(f"[send_otp_sms] OTP SMS sent successfully to {phone_number}")
        return result
    except Exception as e:
        logger.error(f"[send_otp_sms] Failed to send OTP SMS to {phone_number}: {e}")
        raise
