import os
import boto3

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

# DynamoDB tables
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)

# Booking tables
AVAILABILITY_TABLE = dynamodb.Table("DoctorAvailabilityTable")
APPOINTMENTS_TABLE = dynamodb.Table("AppointmentsTable")

# Subscription tables
SUBSCRIPTION_PLANS_TABLE = dynamodb.Table(os.environ.get("SUBSCRIPTION_PLANS_TABLE", "SubscriptionPlans"))
USER_DOCTOR_SUBSCRIPTIONS_TABLE = dynamodb.Table(os.environ.get("USER_DOCTOR_SUBSCRIPTIONS_TABLE", "UserDoctorSubscriptions"))

