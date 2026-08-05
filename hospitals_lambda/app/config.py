import os
import boto3
# from dotenv import load_dotenv
# load_dotenv()

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
SMS_COUNTRY_CODE = os.environ.get("SMS_COUNTRY_CODE", "+91")
S3_BUCKET = os.environ.get("S3_BUCKET", "kokoro-doctor")
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)

HOSPITALS_TABLE = dynamodb.Table(os.environ.get("HOSPITALS_TABLE"))
USERS_TABLE = dynamodb.Table(os.environ.get("USERS_TABLE", "Users"))
DOCTORS_TABLE = dynamodb.Table(os.environ.get("DOCTORS_TABLE", "Doctors"))
AUTH_TABLE = dynamodb.Table(os.environ.get("AUTH_TABLE", "AuthTable"))
DOCTOR_AVAILABILITY_TABLE = dynamodb.Table(
    os.environ.get("DOCTOR_AVAILABILITY_TABLE", "DoctorAvailabilityTable")
)

# Junction tables for the user <-> doctor <-> hospital many-to-many graph.
USER_HOSPITAL_TABLE = dynamodb.Table(os.environ.get("USER_HOSPITAL_TABLE", "UserHospital"))
DOCTOR_HOSPITAL_TABLE = dynamodb.Table(os.environ.get("DOCTOR_HOSPITAL_TABLE", "DoctorHospital"))
USER_DOCTOR_TABLE = dynamodb.Table(os.environ.get("USER_DOCTOR_TABLE", "UserDoctor"))

# MedilockerDocuments table — patient docs are stored here (same table as medilocker)
DOCUMENTS_TABLE = dynamodb.Table(os.environ.get("DOCUMENTS_TABLE", "MedilockerDocuments"))

JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "8"))

# S3 and SQS clients for patient document upload + async OCR
s3_client = boto3.client("s3", region_name=AWS_REGION)
sqs_client = boto3.client("sqs", region_name=AWS_REGION)

# S3 prefix — matches medilocker so OCR worker path logic is consistent
MEDILOCKER_S3_PREFIX = "Medilocker/Users/"

# SQS queue URL for async OCR jobs (OCRWorkerLambda consumer)
OCR_QUEUE_URL = os.environ.get("OCR_QUEUE_URL", "")
