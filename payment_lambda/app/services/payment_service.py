"""
Payment Service - handles Razorpay payment operations
"""
import json
import datetime
import hmac
import hashlib
from decimal import Decimal
from typing import Optional
from fastapi import HTTPException, Request
from botocore.exceptions import ClientError
import razorpay.errors
from app.config import (
    razorpay_client,
    PAYMENTS_TABLE,
    SUBSCRIPTION_PLANS_TABLE,
    USER_DOCTOR_SUBSCRIPTIONS_TABLE,
    lambda_client,
    SUBSCRIPTION_SERVICE_LAMBDA_NAME,
    WEBHOOK_SECRET,
    PLATFORM_FEE_PERCENTAGE
)
from app.services.earnings_service import create_earnings_entry
from boto3.dynamodb.conditions import Key, Attr
from app.utils.email_utils import send_admin_payment_notification

from app.logger import get_logger
logger = get_logger(__name__)


def get_subscription_plan(plan_id: str) -> Optional[dict]:
    """
    Get a subscription plan by plan_id.
    
    IMPORTANT: Always fetches plan data from database. Never derives plan attributes
    (price, validity, etc.) from plan_id format. The database is ALWAYS the source of truth.
    plan_id format (e.g., PLAN_999_30D_ALL) is for human readability only.
    """
    try:
        response = SUBSCRIPTION_PLANS_TABLE.get_item(Key={"plan_id": plan_id})
        return response.get("Item")
    except ClientError as e:
        logger.error(f"DynamoDB error fetching plan: {e}")
        raise HTTPException(500, "Failed to fetch subscription plan")


def get_subscription_by_payment_id(payment_id: str) -> Optional[dict]:
    """
    Get subscription by payment_id using GSI.
    Returns the subscription if found, None otherwise.
    """
    try:
        response = USER_DOCTOR_SUBSCRIPTIONS_TABLE.query(
            IndexName="GSI_PaymentSubscription",
            KeyConditionExpression=Key("payment_id").eq(payment_id)
        )
        items = response.get("Items", [])
        if items:
            # Return the first subscription found (should be only one per payment_id)
            return items[0]
        return None
    except ClientError as e:
        logger.error(f"DynamoDB error fetching subscription by payment_id: {e}")
        # Don't raise exception here, just return None - let caller handle it
        return None


def create_payment_link(plan_id: str, user_id: str = None, doctor_id: str = None) -> dict:
    """
    Create a Razorpay payment link.
    Fetches plan by plan_id from database and uses plan price from database.
    Never derives price from plan_id format - database is the source of truth.
    
    Args:
        plan_id: Plan identifier (human-readable format like PLAN_999_30D_ALL).
                 This is ONLY an identifier - price is fetched from database.
    """
    try:
        # Fetch plan details
        plan = get_subscription_plan(plan_id)
        if not plan:
            raise HTTPException(404, "Subscription plan not found")
        
        if not plan.get("is_active", False):
            raise HTTPException(400, "Subscription plan is not active")
        
        # Get amount from plan (convert Decimal to float if needed)
        plan_price = plan.get("price")
        if plan_price is None:
            raise HTTPException(400, "Plan price is not set")
        
        # Convert Decimal to float if needed
        if isinstance(plan_price, Decimal):
            amount = float(plan_price)
        else:
            amount = float(plan_price)
        
        if amount <= 0:
            raise HTTPException(400, "Invalid plan price")
        
        amount_in_paise = int(amount * 100)
        
        payment_link_data = {
            "amount": amount_in_paise,
            "currency": "INR",
            "description": f"Payment for plan: {plan_id}",
            "callback_url": "https://kokoro.doctor/patient/Doctors/DoctorsInfoWithBooking",
            "callback_method": "get",
            "notes": { "plan_id": plan_id,
                        "user_id": user_id,
                        "doctor_id": doctor_id

            }
        }
        
        payment_link = razorpay_client.payment_link.create(payment_link_data)
        
        logger.info(f"Created payment link for plan_id: {plan_id}, amount: {amount} (from plan)")
        
        return {
            "message": "Payment link created successfully",
            "payment_link": payment_link["short_url"],
            "plan_id": plan_id,
            "amount": amount  # Return the amount used for transparency
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating payment link: {str(e)}", exc_info=True)
        raise HTTPException(500, f"Failed to create payment link: {str(e)}")


def verify_payment(
    payment_id: str,
    plan_id: Optional[str] = None,
    user_id: Optional[str] = None,
    doctor_id: Optional[str] = None
) -> dict:
    """
    Verify payment with Razorpay and store in DynamoDB.
    If plan_id is provided, validates that payment amount matches plan price from database.
    Never derives plan price from plan_id format - always fetches from database.
    If subscription metadata is provided, creates subscription after successful payment.
    
    Args:
        plan_id: Plan identifier (human-readable format like PLAN_999_30D_ALL).
                 This is ONLY an identifier - price is fetched from database for validation.
    """
    try:
        # Validate payment_id before making API call
        if not payment_id or not payment_id.strip():
            raise HTTPException(400, "Payment ID is required and cannot be empty")
        
        # Fetch payment details from Razorpay
        payment_details = razorpay_client.payment.fetch(payment_id)
        order_id = payment_details.get("order_id", "N/A")
        status = payment_details.get("status", "failed")
        amount_paid_paise = payment_details.get("amount", 0)
        amount_paid = Decimal(amount_paid_paise) / Decimal(100)
        
        # If plan_id is provided, validate payment amount matches plan price
        if plan_id:
            plan = get_subscription_plan(plan_id)
            if not plan:
                raise HTTPException(404, "Subscription plan not found")
            
            plan_price = plan.get("price")
            if plan_price is None:
                raise HTTPException(400, "Plan price is not set")
            
            # Convert Decimal to Decimal for comparison
            if isinstance(plan_price, Decimal):
                expected_amount = plan_price
            else:
                expected_amount = Decimal(str(plan_price))
            
            # Validate amount matches (allow small floating point differences)
            amount_diff = abs(float(amount_paid) - float(expected_amount))
            if amount_diff > 0.01:  # Allow 1 paisa difference for floating point precision
                logger.error(
                    f"Payment amount mismatch: paid {amount_paid}, expected {expected_amount} "
                    f"for plan_id {plan_id}, payment_id {payment_id}"
                )
                raise HTTPException(
                    400,
                    f"Payment amount mismatch: paid {amount_paid}, expected {expected_amount}. "
                    "Payment verification failed."
                )
            
            logger.info(
                f"Payment amount validated: {amount_paid} matches plan price {expected_amount} "
                f"for plan_id {plan_id}"
            )
        
        # Generate Invoice if payment is successful
        invoice_url = None
        if status == "captured":
            invoice_url = generate_invoice_url(payment_id)
        
        # Check if payment record already exists to preserve admin_email_sent flag
        existing_payment = None
        try:
            existing_response = PAYMENTS_TABLE.get_item(Key={"payment_id": payment_id})
            existing_payment = existing_response.get("Item")
        except ClientError as e:
            logger.warning(f"Could not check existing payment record: {e}")
        
        # Store Transaction Data in DynamoDB
        # Include user_id, doctor_id, plan_id to identify who made the payment
        payment_data = {
            "payment_id": payment_id,
            "order_id": order_id,
            "amount": amount_paid,
            "currency": "INR",
            "status": status,
            "timestamp": str(datetime.datetime.utcnow()),
            "invoice_url": invoice_url
        }
        
        # Add subscription metadata if provided (for identifying payment purpose)
        if user_id:
            payment_data["user_id"] = user_id
        if doctor_id:
            payment_data["doctor_id"] = doctor_id
        if plan_id:
            payment_data["plan_id"] = plan_id
        
        # Preserve admin_email_sent flag if it exists (for idempotency)
        if existing_payment and existing_payment.get("admin_email_sent"):
            payment_data["admin_email_sent"] = existing_payment["admin_email_sent"]
        
        try:
            PAYMENTS_TABLE.put_item(Item=payment_data)
        except ClientError as e:
            logger.error(f"DynamoDB error storing payment: {e}")
            raise HTTPException(500, "Failed to store payment record")
        
        # If payment is successful and subscription metadata is provided, handle subscription
        subscription_id = None
        subscription_message = None
        if status == "captured" and plan_id and user_id and doctor_id:
            # Check if subscription already exists for this payment_id
            existing_subscription = get_subscription_by_payment_id(payment_id)
            if existing_subscription:
                subscription_id = existing_subscription.get("subscription_id")
                subscription_message = f"Payment is already linked to subscription {subscription_id}. No new subscription created."
                logger.info(f"Payment {payment_id} already linked to subscription {subscription_id}")
            else:
                # Create new subscription
                try:
                    subscription_id = create_subscription_after_payment(
                        user_id=user_id,
                        doctor_id=doctor_id,
                        plan_id=plan_id,
                        payment_id=payment_id
                    )
                    subscription_message = f"Subscription {subscription_id} created successfully for payment {payment_id}"
                    logger.info(f"Created subscription {subscription_id} for payment {payment_id}")
                except Exception as sub_error:
                    logger.error(f"Failed to create subscription: {str(sub_error)}", exc_info=True)
                    # Don't fail the payment verification if subscription creation fails
                    # The subscription can be created manually later
                    subscription_message = f"Payment verified but subscription creation failed: {str(sub_error)}"
            
            # Create earnings ledger entry for doctor (only if doctor_id is present)
            if doctor_id:
                try:
                    create_earnings_entry(
                        doctor_id=doctor_id,
                        user_id=user_id,
                        subscription_id=subscription_id or "",
                        payment_id=payment_id,
                        gross_amount=amount_paid,
                        platform_fee_percentage=PLATFORM_FEE_PERCENTAGE
                    )
                    logger.info(f"Created earnings entry for doctor {doctor_id}, payment {payment_id}")
                except Exception as earnings_error:
                    logger.error(f"Failed to create earnings entry: {str(earnings_error)}", exc_info=True)
                    # Don't fail payment verification if earnings entry creation fails
                    # Earnings can be created manually later if needed
        
        # Send admin email notification for captured payments (idempotent)
        if status == "captured":
            try:
                # Use update_item with condition to atomically set admin_email_sent flag
                # This ensures email is sent only once, even with webhook retries
                try:
                    update_response = PAYMENTS_TABLE.update_item(
                        Key={"payment_id": payment_id},
                        UpdateExpression="SET admin_email_sent = :true",
                        ConditionExpression=Attr("admin_email_sent").not_exists() | Attr("admin_email_sent").eq(False),
                        ExpressionAttributeValues={":true": True},
                        ReturnValues="NONE"
                    )
                    # If update succeeds, it means admin_email_sent was not set, so send email
                    logger.info(f"Admin email flag set for payment {payment_id}, sending notification email")
                    send_admin_payment_notification(
                        payment_id=payment_id,
                        order_id=order_id,
                        amount=amount_paid,
                        status=status,
                        user_id=user_id,
                        doctor_id=doctor_id,
                        plan_id=plan_id,
                        invoice_url=invoice_url,
                        timestamp=payment_data.get("timestamp")
                    )
                    logger.info(f"Admin notification email sent successfully for payment {payment_id}")
                except ClientError as e:
                    # If condition check fails, it means admin_email_sent already exists and is True
                    # This is expected for webhook retries - email was already sent
                    if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                        logger.info(f"Admin email already sent for payment {payment_id}, skipping duplicate notification")
                    else:
                        raise
            except Exception as email_error:
                # Don't fail payment verification if email sending fails
                # Log error but continue with payment processing
                logger.error(f"Failed to send admin notification email for payment {payment_id}: {str(email_error)}", exc_info=True)
        
        response = {
            "message": "Payment processed",
            "order_id": order_id,
            "payment_id": payment_id,
            "status": status,
            "invoice_url": invoice_url
        }
        
        if subscription_id:
            response["subscription_id"] = subscription_id
        if subscription_message:
            response["subscription_message"] = subscription_message
        
        return response
        
    except razorpay.errors.BadRequestError as e:
        error_msg = str(e)
        if "Authentication failed" in error_msg:
            logger.error(f"Razorpay authentication failed. Check RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET")
            raise HTTPException(500, "Payment gateway authentication failed. Please check configuration.")
        elif "does not exist" in error_msg:
            logger.error(f"Payment ID not found: {payment_id}")
            raise HTTPException(404, f"Payment ID '{payment_id}' not found in Razorpay")
        else:
            logger.error(f"Razorpay BadRequestError: {error_msg}")
            raise HTTPException(400, f"Invalid payment request: {error_msg}")
    except razorpay.errors.ServerError as e:
        logger.error(f"Razorpay server error: {str(e)}", exc_info=True)
        raise HTTPException(502, f"Payment gateway error: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error verifying payment: {str(e)}", exc_info=True)
        raise HTTPException(500, f"Failed to verify payment: {str(e)}")


def generate_invoice_url(payment_id: str) -> str:
    """
    Generate invoice URL for a payment.
    """
    base_url = "https://kokoro.doctor/payment/invoices"
    return f"{base_url}/{payment_id}.pdf"


def create_subscription_after_payment(user_id: str, doctor_id: str, plan_id: str, payment_id: str) -> Optional[str]:
    """
    Create a subscription after successful payment by invoking the subscription service Lambda.
    """
    subscription_payload = {
        "user_id": user_id,
        "doctor_id": doctor_id,
        "plan_id": plan_id,
        "payment_id": payment_id
    }
    
    # Create API Gateway event structure for Mangum adapter
    # Mangum requires a complete API Gateway event with requestContext
    lambda_event = {
        "httpMethod": "POST",
        "path": "/booking/subscriptions",
        "resource": "/booking/subscriptions",
        "pathParameters": None,
        "queryStringParameters": None,
        "headers": {
            "Content-Type": "application/json",
            "Host": "localhost",
            "User-Agent": "payment-service-lambda"
        },
        "multiValueHeaders": {},
        "body": json.dumps(subscription_payload),
        "isBase64Encoded": False,
        "requestContext": {
            "requestId": "payment-service-invocation",
            "stage": "prod",
            "resourceId": "subscriptions",
            "resourcePath": "/booking/subscriptions",
            "httpMethod": "POST",
            "requestTime": datetime.datetime.utcnow().strftime("%d/%b/%Y:%H:%M:%S +0000"),
            "requestTimeEpoch": int(datetime.datetime.utcnow().timestamp() * 1000),
            "protocol": "HTTP/1.1",
            "accountId": "123456789012",
            "apiId": "payment-service"
        }
    }
    
    try:
        response = lambda_client.invoke(
            FunctionName=SUBSCRIPTION_SERVICE_LAMBDA_NAME,
            InvocationType="RequestResponse",
            Payload=json.dumps(lambda_event)
        )
        
        # Read the response payload once
        response_payload = json.loads(response["Payload"].read())
        
        # Check if Lambda invocation had an error (FunctionError in response metadata)
        if "FunctionError" in response:
            error_type = response.get("FunctionError", "Unknown")
            error_message = response_payload.get("errorMessage", "Unknown Lambda error")
            error_type_name = response_payload.get("errorType", "UnknownError")
            logger.error(
                f"Subscription service Lambda error ({error_type_name}, FunctionError: {error_type}): {error_message}. "
                f"Full response: {response_payload}"
            )
            raise Exception(f"Failed to create subscription: {error_type_name} - {error_message}")
        
        # Check if this is a Lambda error response (has errorMessage/errorType in payload)
        if "errorMessage" in response_payload or "errorType" in response_payload:
            error_message = response_payload.get("errorMessage", "Unknown Lambda error")
            error_type = response_payload.get("errorType", "UnknownError")
            logger.error(
                f"Subscription service Lambda error ({error_type}): {error_message}. "
                f"Full response: {response_payload}"
            )
            raise Exception(f"Failed to create subscription: {error_type} - {error_message}")
        
        # Parse the response (Lambda returns statusCode and body for API Gateway format)
        status_code = response_payload.get("statusCode")
        # Handle both 200 (existing subscription) and 201 (new subscription)
        if status_code in [200, 201]:
            body = json.loads(response_payload.get("body", "{}"))
            subscription_id = body.get("subscription_id")
            if not subscription_id:
                logger.warning(f"Subscription service returned {status_code} but no subscription_id in response")
            return subscription_id
        else:
            # Parse error body if it's a JSON string
            error_body = response_payload.get("body", "{}")
            try:
                if isinstance(error_body, str):
                    error_body = json.loads(error_body)
                error_detail = error_body.get("error") or error_body.get("message") or str(error_body)
            except (json.JSONDecodeError, AttributeError):
                error_detail = str(error_body) if error_body else "Unknown error"
            
            logger.error(
                f"Subscription service returned {status_code}: {error_detail}. "
                f"Full response: {response_payload}"
            )
            raise Exception(f"Failed to create subscription: {error_detail}")
            
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Lambda response JSON: {str(e)}", exc_info=True)
        raise Exception(f"Failed to parse subscription service response: {str(e)}")
    except ClientError as e:
        logger.error(f"AWS Lambda invocation error: {str(e)}", exc_info=True)
        raise Exception(f"Failed to invoke subscription service: {str(e)}")
    except Exception as e:
        logger.error(f"Error invoking subscription service Lambda: {str(e)}", exc_info=True)
        raise


async def process_razorpay_webhook(request: Request) -> dict:
    """
    Process Razorpay webhook events.
    Verifies the signature and handles payment.captured events.
    
    Args:
        request: FastAPI Request object containing webhook payload
        
    Returns:
        dict: Status response for Razorpay
    """
    try:
        # 1. Get raw body and signature
        body_bytes = await request.body()
        signature = request.headers.get("x-razorpay-signature")

        if not signature:
            logger.warning("Webhook missing signature")
            # Return 200 to Razorpay even on error to stop them from retrying
            return {"status": "ignored"}

        # 2. Verify Signature
        try:
            generated_signature = hmac.new(
                key=WEBHOOK_SECRET.encode(),
                msg=body_bytes,
                digestmod=hashlib.sha256
            ).hexdigest()

            if generated_signature != signature:
                logger.error("Invalid Webhook Signature")
                raise HTTPException(400, "Invalid signature")
        except Exception as e:
            logger.error(f"Signature verification failed: {e}")
            raise HTTPException(400, "Verification failed")

        # 3. Process the Event
        data = json.loads(body_bytes.decode('utf-8'))
        event_type = data.get("event")

        # Listen for 'payment.captured' (Works for Links, Buttons, everything)
        if event_type == "payment.captured":
            payment_entity = data["payload"]["payment"]["entity"]
            
            # Extract details from 'notes' (which we sent while creating link)
            notes = payment_entity.get("notes", {})
            user_id = notes.get("user_id")
            plan_id = notes.get("plan_id")
            doctor_id = notes.get("doctor_id") # Optional
            
            razorpay_payment_id = payment_entity.get("id")
            
            logger.info(f"🔔 Webhook received: Payment {razorpay_payment_id} captured for User {user_id}")

            if user_id and plan_id:
                # Calls the existing verify_payment logic internally
                # This updates the database automatically
                try:
                    verify_payment(
                        payment_id=razorpay_payment_id,
                        plan_id=plan_id,
                        user_id=user_id,
                        doctor_id=doctor_id
                    )
                    logger.info(f"✅ Auto-verified payment via Webhook for User: {user_id}")
                except Exception as e:
                    logger.error(f"❌ Failed to process webhook db update: {e}")
            else:
                logger.warning("Webhook received but missing user_id or plan_id in notes")

        # 4. Acknowledge Receipt (Always return 200 OK)
        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Webhook processing error: {str(e)}")
        # We still return 200 OK to Razorpay so they don't keep retrying failed logic
        return {"status": "error", "detail": str(e)}

