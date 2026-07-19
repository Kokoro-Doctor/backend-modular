import os
import boto3
from typing import List
# from dotenv import load_dotenv
# load_dotenv()

# Environment variables (required ones left as KeyError to fail fast in prod)
USERS_TABLE = os.environ["USERS_TABLE"]
DOCTORS_TABLE = os.environ["DOCTORS_TABLE"]
USER_HOSPITAL_TABLE = os.environ.get("USER_HOSPITAL_TABLE", "UserHospital")
AUTH_TABLE = os.environ["AUTH_TABLE"]
AUTH_TOKENS_TABLE = os.environ["AUTH_TOKENS_TABLE"]
SESSIONS_TABLE_NAME = os.environ["SESSIONS_TABLE"]

BREVO_SMTP_USER = os.environ["BREVO_SMTP_USER"]
BREVO_SMTP_KEY = os.environ["BREVO_SMTP_KEY"]
BREVO_SMTP_SERVER = os.environ["BREVO_SMTP_SERVER"]
BREVO_SMTP_PORT = int(os.environ["BREVO_SMTP_PORT"])

SMS_AWS_REGION = os.environ.get("SMS_AWS_REGION", "ap-south-1")
SMS_COUNTRY_CODE = os.environ.get("SMS_COUNTRY_CODE", "+91")
JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ISSUER = os.environ.get("JWT_ISSUER", "kokoro.doctor")
JWT_EXP_MINUTES = int(os.environ.get("JWT_EXP_MINUTES", "1440"))
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")

# Rate limiting defaults (all values are seconds/attempts)
MOBILE_OTP_RATE_LIMIT_MAX_ATTEMPTS = int(
    os.environ.get("MOBILE_OTP_RATE_LIMIT_MAX_ATTEMPTS", "5")
)
MOBILE_OTP_RATE_LIMIT_WINDOW_SECONDS = int(
    os.environ.get("MOBILE_OTP_RATE_LIMIT_WINDOW_SECONDS", str(15 * 60))
)

# Google client IDs list (kept same approach as your original)
CLIENT_IDS: List[str] = [
    "569847732356-mv68e01dvj204ouqjj0k48a8hq54knh3.apps.googleusercontent.com",
    "569847732356-2h2oqgj6tq0t8cflbstjiedafveg57c5.apps.googleusercontent.com",
    "569847732356-rl6pnkut18s91cvsfipcuhlkptpoj8fh.apps.googleusercontent.com",
    "569847732356-v4pm3kfbrb0i3adcbchn82qcl7ua1cm8.apps.googleusercontent.com",
]

# Admin key for internal operations (set via environment variable)
ADMIN_KEY = os.environ.get("ADMIN_KEY", "change-me-in-production")

# DynamoDB resource & table handles
dynamodb = boto3.resource("dynamodb", region_name="ap-south-1")
users_table = dynamodb.Table(USERS_TABLE)
doctors_table = dynamodb.Table(DOCTORS_TABLE)
user_hospital_table = dynamodb.Table(USER_HOSPITAL_TABLE)
auth_table = dynamodb.Table(AUTH_TABLE)
auth_tokens_table = dynamodb.Table(AUTH_TOKENS_TABLE)
sessions_table = dynamodb.Table(SESSIONS_TABLE_NAME)

# Additional tables for account deletion
appointments_table = dynamodb.Table("AppointmentsTable")
availability_table = dynamodb.Table("DoctorAvailabilityTable")
chat_table = dynamodb.Table("ChatHistory")
payments_table = dynamodb.Table("PaymentsTable")
medilocker_documents_table = dynamodb.Table("MedilockerDocuments")
user_doctor_subscriptions_table = dynamodb.Table("UserDoctorSubscriptions")
user_doctor_table = dynamodb.Table(os.environ.get("USER_DOCTOR_TABLE", "UserDoctor"))
user_doctor_relations_table = dynamodb.Table("UserDoctorRelations")
doctor_earnings_table = dynamodb.Table("DoctorEarningsLedger")
doctor_payouts_table = dynamodb.Table("DoctorPayoutsTable")
abha_accounts_table = dynamodb.Table("AbhaAccounts")
abdm_transactions_table = dynamodb.Table("AbdmTransactions")

# S3 client for file deletion
s3_client = boto3.client("s3", region_name=SMS_AWS_REGION)
S3_BUCKET = os.environ.get("S3_BUCKET", "kokoro-doctor")
