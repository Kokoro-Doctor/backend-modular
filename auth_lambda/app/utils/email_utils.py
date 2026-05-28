"""
Email utility - handles sending emails via Brevo SMTP.
Based on testmail.py implementation.
Uses Brevo credentials from config.
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app import config
from app.logger import get_logger

logger = get_logger(__name__)

# Sender configuration (matching testmail.py)
SENDER_EMAIL = "verify@kokoro.doctor"
SENDER_NAME = "Kokoro Doctor"
OTP_VALIDITY_MINUTES = 5


def send_otp_email(email: str, otp: str):
    """
    Send OTP via email using Brevo SMTP.
    Uses credentials from config (BREVO_SMTP_USER, BREVO_SMTP_KEY, etc.).
    Raises exception if email sending fails.
    Based on testmail.py implementation.
    """
    try:
        # Get Brevo credentials from config
        smtp_user = config.BREVO_SMTP_USER
        smtp_password = config.BREVO_SMTP_KEY
        smtp_server = config.BREVO_SMTP_SERVER
        smtp_port = config.BREVO_SMTP_PORT

        msg = MIMEMultipart("alternative")
        msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
        msg["To"] = email
        msg["Subject"] = "Your Kokoro Verification Code"

        html_body = f"""
        <html>
            <body>
                <h2>Kokoro Doctor Verification</h2>
                <p>Your OTP is:</p>
                <h1 style="letter-spacing: 4px;">{otp}</h1>
                <p>This OTP is valid for {OTP_VALIDITY_MINUTES} minutes.</p>
                <p>If you did not request this, please ignore this email.</p>
            </body>
        </html>
        """

        msg.attach(MIMEText(html_body, "html"))

        logger.info(f"[send_otp_email] Sending OTP email to {email} via {smtp_server}:{smtp_port}")
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(SENDER_EMAIL, email, msg.as_string())

        logger.info(f"[send_otp_email] OTP email sent successfully to {email}")
        return True
    except Exception as e:
        logger.error(f"[send_otp_email] Failed to send OTP email to {email}: {e}")
        raise

