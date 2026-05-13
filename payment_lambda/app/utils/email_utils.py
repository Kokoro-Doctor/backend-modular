"""
Email utility - handles sending admin payment notification emails via Brevo SMTP.
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from decimal import Decimal

from app.config import (
    BREVO_SMTP_USER,
    BREVO_SMTP_KEY,
    BREVO_SMTP_SERVER,
    BREVO_SMTP_PORT,
    ADMIN_EMAIL
)
from app.logger import get_logger

logger = get_logger(__name__)

# Sender configuration
SENDER_EMAIL = "verify@kokoro.doctor"
SENDER_NAME = "Kokoro Doctor"


def send_admin_payment_notification(
    payment_id: str,
    order_id: str,
    amount: Decimal,
    status: str,
    user_id: Optional[str] = None,
    doctor_id: Optional[str] = None,
    plan_id: Optional[str] = None,
    invoice_url: Optional[str] = None,
    timestamp: Optional[str] = None
) -> bool:
    """
    Send admin notification email when payment is successfully captured.
    
    Args:
        payment_id: Razorpay payment ID
        order_id: Razorpay order ID
        amount: Payment amount (Decimal)
        status: Payment status
        user_id: Optional user ID
        doctor_id: Optional doctor ID
        plan_id: Optional plan ID
        invoice_url: Optional invoice URL
        timestamp: Optional payment timestamp
        
    Returns:
        bool: True if email sent successfully, False otherwise
        
    Raises:
        Exception: If email sending fails (caller should handle gracefully)
    """
    try:
        # Validate email configuration
        if not BREVO_SMTP_USER or not BREVO_SMTP_KEY:
            logger.warning("Email credentials not configured. Skipping admin notification.")
            return False
            
        if not ADMIN_EMAIL:
            logger.warning("ADMIN_EMAIL not configured. Skipping admin notification.")
            return False

        # Format amount for display
        amount_str = f"₹{float(amount):.2f}"
        
        # Build email content
        msg = MIMEMultipart("alternative")
        msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
        msg["To"] = ADMIN_EMAIL
        msg["Subject"] = f"Payment Captured: {payment_id}"

        # Build HTML body with payment details
        html_body = f"""
        <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <h2 style="color: #2c3e50;">Payment Successfully Captured</h2>
                <div style="background-color: #f8f9fa; padding: 20px; border-radius: 5px; margin: 20px 0;">
                    <h3 style="color: #27ae60; margin-top: 0;">Payment Details</h3>
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr>
                            <td style="padding: 8px; font-weight: bold; width: 150px;">Payment ID:</td>
                            <td style="padding: 8px;">{payment_id}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px; font-weight: bold;">Order ID:</td>
                            <td style="padding: 8px;">{order_id}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px; font-weight: bold;">Amount:</td>
                            <td style="padding: 8px; color: #27ae60; font-size: 18px; font-weight: bold;">{amount_str}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px; font-weight: bold;">Status:</td>
                            <td style="padding: 8px;"><span style="background-color: #27ae60; color: white; padding: 4px 8px; border-radius: 3px;">{status.upper()}</span></td>
                        </tr>
        """
        
        if user_id:
            html_body += f"""
                        <tr>
                            <td style="padding: 8px; font-weight: bold;">User ID:</td>
                            <td style="padding: 8px;">{user_id}</td>
                        </tr>
            """
        
        if doctor_id:
            html_body += f"""
                        <tr>
                            <td style="padding: 8px; font-weight: bold;">Doctor ID:</td>
                            <td style="padding: 8px;">{doctor_id}</td>
                        </tr>
            """
        
        if plan_id:
            html_body += f"""
                        <tr>
                            <td style="padding: 8px; font-weight: bold;">Plan ID:</td>
                            <td style="padding: 8px;">{plan_id}</td>
                        </tr>
            """
        
        if invoice_url:
            html_body += f"""
                        <tr>
                            <td style="padding: 8px; font-weight: bold;">Invoice:</td>
                            <td style="padding: 8px;"><a href="{invoice_url}" style="color: #3498db;">View Invoice</a></td>
                        </tr>
            """
        
        if timestamp:
            html_body += f"""
                        <tr>
                            <td style="padding: 8px; font-weight: bold;">Timestamp:</td>
                            <td style="padding: 8px;">{timestamp}</td>
                        </tr>
            """
        
        html_body += """
                    </table>
                </div>
                <p style="color: #7f8c8d; font-size: 12px;">This is an automated notification from Kokoro Doctor payment system.</p>
            </body>
        </html>
        """

        msg.attach(MIMEText(html_body, "html"))

        logger.info(f"[send_admin_payment_notification] Sending admin notification email for payment {payment_id} to {ADMIN_EMAIL}")
        
        with smtplib.SMTP(BREVO_SMTP_SERVER, BREVO_SMTP_PORT) as server:
            server.starttls()
            server.login(BREVO_SMTP_USER, BREVO_SMTP_KEY)
            server.sendmail(SENDER_EMAIL, ADMIN_EMAIL, msg.as_string())

        logger.info(f"[send_admin_payment_notification] Admin notification email sent successfully for payment {payment_id}")
        return True
        
    except Exception as e:
        logger.error(f"[send_admin_payment_notification] Failed to send admin notification email for payment {payment_id}: {e}", exc_info=True)
        raise
