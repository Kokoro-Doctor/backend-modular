import os
import boto3
from dotenv import load_dotenv
load_dotenv()

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

# DynamoDB
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
USERS_TABLE = os.environ["USERS_TABLE"]
users_table = dynamodb.Table(USERS_TABLE)

# S3
s3_client = boto3.client("s3", region_name=AWS_REGION)
S3_BUCKET = os.environ.get("S3_BUCKET", "kokoro-doctor")
ABHA_CARD_PREFIX = "abha-cards/"

# JWT (verify-only — tokens are issued by auth_lambda)
JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGORITHM = "HS256"
JWT_ISSUER = os.environ.get("JWT_ISSUER", "kokoro-doctor")

# ABDM credentials and endpoints
ABDM_CLIENT_ID = os.environ["ABDM_CLIENT_ID"]
ABDM_CLIENT_SECRET = os.environ["ABDM_CLIENT_SECRET"]
ABDM_GATEWAY_BASE_URL = os.environ.get("ABDM_GATEWAY_BASE_URL", "https://dev.abdm.gov.in")
ABDM_ABHA_BASE_URL = os.environ.get("ABDM_ABHA_BASE_URL", "https://abhasbx.abdm.gov.in")
ABDM_X_CM_ID = os.environ.get("ABDM_X_CM_ID", "sbx")
