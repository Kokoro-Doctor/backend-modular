import os
import boto3
# from dotenv import load_dotenv
# load_dotenv()

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

# DynamoDB tables
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
AVAILABILITY_TABLE = dynamodb.Table("DoctorAvailabilityTable")
BOOKING_TABLE = dynamodb.Table("DoctorBookingsTable")
