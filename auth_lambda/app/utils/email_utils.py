import smtplib
from email.message import EmailMessage
from app import config
from app.logger import get_logger

logger = get_logger(__name__)

def send_brevo_email(to_email: str, subject: str, body: str, from_email: str = "verify@kokoro.doctor"):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(config.BREVO_SMTP_SERVER, config.BREVO_SMTP_PORT) as smtp:
            smtp.starttls()
            smtp.login(config.BREVO_SMTP_USER, config.BREVO_SMTP_KEY)
            smtp.send_message(msg)
        logger.info(f"[email_utils] Sent email to {to_email} subject={subject}")
    except Exception as e:
        logger.exception(f"[email_utils] Failed to send email to {to_email}")
        raise
