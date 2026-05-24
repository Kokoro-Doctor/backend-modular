import os
import boto3
# from dotenv import load_dotenv
# load_dotenv()

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)

ABHA_TABLE = os.environ.get("ABHA_TABLE", "AbhaAccounts")
abha_table = dynamodb.Table(ABHA_TABLE)

# ABDM credentials and endpoints
ABDM_CLIENT_ID        = os.environ["ABDM_CLIENT_ID"]
ABDM_CLIENT_SECRET    = os.environ["ABDM_CLIENT_SECRET"]
ABDM_GATEWAY_BASE_URL = os.environ.get("ABDM_GATEWAY_BASE_URL", "https://dev.abdm.gov.in")
ABDM_ABHA_BASE_URL    = os.environ.get("ABDM_ABHA_BASE_URL",    "https://abhasbx.abdm.gov.in")
ABDM_X_CM_ID          = os.environ.get("ABDM_X_CM_ID",          "sbx")

# Kokoro JWT — used to verify the caller's identity on profile/card endpoints
JWT_SECRET    = os.environ.get("JWT_SECRET", "replace-me")
JWT_ISSUER    = os.environ.get("JWT_ISSUER", "kokoro.doctor.auth")
JWT_ALGORITHM = "HS256"
