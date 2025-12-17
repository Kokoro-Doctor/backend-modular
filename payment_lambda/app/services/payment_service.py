"""
Payment Service - handles Razorpay payment operations
"""
import json
import datetime
from decimal import Decimal
from typing import Optional
from fastapi import HTTPException
from botocore.exceptions import ClientError
import razorpay.errors
from app.config import razorpay_client, PAYMENTS_TABLE, SUBSCRIPTION_PLANS_TABLE, USER_DOCTOR_SUBSCRIPTIONS_TABLE, lambda_client, SUBSCRIPTION_SERVICE_LAMBDA_NAME
from boto3.dynamodb.conditions import Key
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


def create_payment_link(plan_id: str) -> dict:
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
            "callback_url": "https://kokoro.doctor/payment-success",
            "callback_method": "get"
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
    base_url = "https://yourwebsite.com/invoices"
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

