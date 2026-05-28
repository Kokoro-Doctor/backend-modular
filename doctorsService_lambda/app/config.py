import os
import boto3
# from dotenv import load_dotenv
# load_dotenv()

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
DYNAMODB = boto3.resource("dynamodb", region_name=AWS_REGION)

DOCTORS_TABLE = DYNAMODB.Table(os.environ["DOCTORS_TABLE"])
USERS_TABLE = DYNAMODB.Table(os.environ["USERS_TABLE"])
HOSPITALS_TABLE = DYNAMODB.Table(os.environ.get("HOSPITALS_TABLE", "Hospitals"))
AVAILABILITY_TABLE = DYNAMODB.Table("DoctorAvailabilityTable")

S3 = boto3.client("s3")
S3_BUCKET = os.getenv("S3_BUCKET", "kokoro-doctor")
