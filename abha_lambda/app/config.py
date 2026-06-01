import os
import boto3
# from dotenv import load_dotenv
# load_dotenv()

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)

ABHA_TABLE = os.environ.get("ABHA_TABLE", "AbhaAccounts")
abha_table = dynamodb.Table(ABHA_TABLE)

HOSPITAL_ABDM_TABLE = os.environ.get("HOSPITAL_ABDM_TABLE", "HospitalAbdmConfig")
hospital_abdm_table = dynamodb.Table(HOSPITAL_ABDM_TABLE)

# Tracking table for ABDM async (callback-based) requests — see abdm_transactions_service
ABDM_TRANSACTIONS_TABLE = os.environ.get("ABDM_TRANSACTIONS_TABLE", "AbdmTransactions")
abdm_transactions_table = dynamodb.Table(ABDM_TRANSACTIONS_TABLE)

# How long a transaction row lives before DynamoDB TTL auto-deletes it
ABDM_TRANSACTION_TTL_DAYS = int(os.environ.get("ABDM_TRANSACTION_TTL_DAYS", "90"))

# ABDM credentials and endpoints
ABDM_CLIENT_ID        = os.environ["ABDM_CLIENT_ID"]
ABDM_CLIENT_SECRET    = os.environ["ABDM_CLIENT_SECRET"]
ABDM_GATEWAY_BASE_URL = os.environ.get("ABDM_GATEWAY_BASE_URL", "https://dev.abdm.gov.in")
ABDM_ABHA_BASE_URL    = os.environ.get("ABDM_ABHA_BASE_URL",    "https://abhasbx.abdm.gov.in")
ABDM_X_CM_ID          = os.environ.get("ABDM_X_CM_ID",          "sbx")

# HIP identity is now stored per-hospital in HospitalAbdmConfig DynamoDB table.
# ABDM_HIP_ID is no longer a global config value.
# Separate host used only for facility registration (3.2.5)
ABDM_FACILITY_REG_BASE_URL    = os.environ.get(
    "ABDM_FACILITY_REG_BASE_URL", "https://apihspsbx.abdm.gov.in"
)
# Our own deployed base URL — registered with ABDM as the bridge callback URL (3.2.4)
KOKORO_WEBHOOK_BASE_URL       = os.environ.get("KOKORO_WEBHOOK_BASE_URL", "")

# Kokoro JWT — used to verify the caller's identity on profile/card endpoints
JWT_SECRET    = os.environ.get("JWT_SECRET", "replace-me")
JWT_ISSUER    = os.environ.get("JWT_ISSUER", "kokoro.doctor.auth")
JWT_ALGORITHM = "HS256"
