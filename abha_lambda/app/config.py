import os
import boto3
# from dotenv import load_dotenv
# load_dotenv()

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)

ABHA_TABLE = os.environ.get("ABHA_TABLE", "AbhaAccounts")
abha_table = dynamodb.Table(ABHA_TABLE)

# Kokoro Users table — shared with auth_lambda / userService_lambda. Used to
# provision a Kokoro user from an ABHA record (see kokoro_user_service) and by
# user_abha_service. GSIs available: "phone-index" (phoneNumber), "email-index".
USERS_TABLE = os.environ.get("USERS_TABLE", "Users")
users_table = dynamodb.Table(USERS_TABLE)

# Default country code for phone normalization (matches auth_lambda).
SMS_COUNTRY_CODE = os.environ.get("SMS_COUNTRY_CODE", "+91")

HOSPITAL_ABDM_TABLE = os.environ.get("HOSPITAL_ABDM_TABLE", "HospitalAbdmConfig")
hospital_abdm_table = dynamodb.Table(HOSPITAL_ABDM_TABLE)

# Tracking table for ABDM async (callback-based) requests — see abdm_transactions_service
ABDM_TRANSACTIONS_TABLE = os.environ.get("ABDM_TRANSACTIONS_TABLE", "AbdmTransactions")
abdm_transactions_table = dynamodb.Table(ABDM_TRANSACTIONS_TABLE)

# Consent artefacts received via the 6.3.1 data-flow callback — see consent_service
CONSENT_ARTEFACTS_TABLE = os.environ.get("CONSENT_ARTEFACTS_TABLE", "ConsentArtefacts")
consent_artefacts_table = dynamodb.Table(CONSENT_ARTEFACTS_TABLE)

# Milestone 3 (HIU) — consent request lifecycle tracking — see hiu_consent_service
HIU_CONSENT_REQUESTS_TABLE = os.environ.get("HIU_CONSENT_REQUESTS_TABLE", "HiuConsentRequests")
hiu_consent_requests_table = dynamodb.Table(HIU_CONSENT_REQUESTS_TABLE)

# Milestone 3 (HIU) — data-flow request state + ephemeral decryption keys — see hiu_data_service
HIU_DATA_REQUESTS_TABLE = os.environ.get("HIU_DATA_REQUESTS_TABLE", "HiuDataRequests")
hiu_data_requests_table = dynamodb.Table(HIU_DATA_REQUESTS_TABLE)

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
# Our own deployed base URL — registered with ABDM as the bridge callback URL (3.2.4).
# Also used to build the HIU dataPushUrl we hand to ABDM (M3 health-information request).
KOKORO_WEBHOOK_BASE_URL       = os.environ.get("KOKORO_WEBHOOK_BASE_URL", "")
# Path under KOKORO_WEBHOOK_BASE_URL where HIPs push encrypted records to us (HIU role).
KOKORO_HIU_DATA_PUSH_PATH     = os.environ.get(
    "KOKORO_HIU_DATA_PUSH_PATH", "/api/v3/hiu/health-information/transfer"
)

# Kokoro JWT — used to verify the caller's identity on profile/card endpoints
JWT_SECRET    = os.environ.get("JWT_SECRET", "replace-me")
JWT_ISSUER    = os.environ.get("JWT_ISSUER", "kokoro.doctor.auth")
JWT_ALGORITHM = "HS256"
