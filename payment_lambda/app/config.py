import os
import boto3
import razorpay

# from dotenv import load_dotenv
# load_dotenv()

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

# Razorpay client - credentials are required
RAZORPAY_KEY_ID = os.environ["RAZORPAY_KEY_ID"]
RAZORPAY_KEY_SECRET = os.environ["RAZORPAY_KEY_SECRET"]
# Razorpay webhook secret
WEBHOOK_SECRET = os.environ["RAZORPAY_WEBHOOK_SECRET"]

# Validate Razorpay credentials before initializing client
if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
    raise ValueError(
        "Razorpay credentials are missing. Please set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET environment variables."
    )

razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

# DynamoDB tables
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
PAYMENTS_TABLE = dynamodb.Table(os.environ.get("DYNAMODB_TABLE_NAME", "PaymentsTable"))
SUBSCRIPTION_PLANS_TABLE = dynamodb.Table(os.environ.get("SUBSCRIPTION_PLANS_TABLE", "SubscriptionPlans"))
USER_DOCTOR_SUBSCRIPTIONS_TABLE = dynamodb.Table(os.environ.get("USER_DOCTOR_SUBSCRIPTIONS_TABLE", "UserDoctorSubscriptions"))
DOCTOR_EARNINGS_LEDGER_TABLE = dynamodb.Table(os.environ.get("DOCTOR_EARNINGS_LEDGER_TABLE", "DoctorEarningsLedger"))

# Platform fee percentage (configurable via environment variable)
PLATFORM_FEE_PERCENTAGE = float(os.environ.get("PLATFORM_FEE_PERCENTAGE", "20.0"))

# Lambda client for invoking subscription service
lambda_client = boto3.client("lambda", region_name=AWS_REGION)
SUBSCRIPTION_SERVICE_LAMBDA_NAME = os.environ.get("SUBSCRIPTION_SERVICE_LAMBDA_NAME", "BookingLambda")



