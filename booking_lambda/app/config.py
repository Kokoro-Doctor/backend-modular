import os
import boto3

# from dotenv import load_dotenv
# load_dotenv()

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

# Admin key for internal operations (set via environment variable)
ADMIN_KEY = os.environ.get("ADMIN_KEY")

# DynamoDB tables
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)

# Booking tables
AVAILABILITY_TABLE = dynamodb.Table("DoctorAvailabilityTable")
APPOINTMENTS_TABLE = dynamodb.Table("AppointmentsTable")

# Subscription tables
SUBSCRIPTION_PLANS_TABLE = dynamodb.Table(os.environ.get("SUBSCRIPTION_PLANS_TABLE", "SubscriptionPlans"))
USER_DOCTOR_SUBSCRIPTIONS_TABLE = dynamodb.Table(os.environ.get("USER_DOCTOR_SUBSCRIPTIONS_TABLE", "UserDoctorSubscriptions"))

# Users table (for patient import - get/create user by phone)
USERS_TABLE = dynamodb.Table(os.environ.get("USERS_TABLE", "Users"))

# User-Doctor relation table (persistent connectivity graph)
USER_DOCTOR_TABLE = dynamodb.Table(os.environ.get("USER_DOCTOR_TABLE", "UserDoctor"))
