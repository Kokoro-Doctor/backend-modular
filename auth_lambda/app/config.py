import os
import boto3
from typing import List
from dotenv import load_dotenv
load_dotenv()

# Environment variables (required ones left as KeyError to fail fast in prod)
USERS_TABLE = os.environ["USERS_TABLE"]
DOCTORS_TABLE = os.environ["DOCTORS_TABLE"]
AUTH_TOKENS_TABLE = os.environ["AUTH_TOKENS_TABLE"]
SESSIONS_TABLE = os.environ.get("SESSIONS_TABLE", "SessionsTable")

BREVO_SMTP_USER = os.environ["BREVO_SMTP_USER"]
BREVO_SMTP_KEY = os.environ["BREVO_SMTP_KEY"]
BREVO_SMTP_SERVER = os.environ["BREVO_SMTP_SERVER"]
BREVO_SMTP_PORT = int(os.environ["BREVO_SMTP_PORT"])

FAST2SMS_API_KEY = os.environ["FAST2SMS_API_KEY"]

# Google client IDs list (kept same approach as your original)
CLIENT_IDS: List[str] = [
    "569847732356-mv68e01dvj204ouqjj0k48a8hq54knh3.apps.googleusercontent.com",
    "569847732356-2h2oqgj6tq0t8cflbstjiedafveg57c5.apps.googleusercontent.com",
    "569847732356-rl6pnkut18s91cvsfipcuhlkptpoj8fh.apps.googleusercontent.com",
    "569847732356-v4pm3kfbrb0i3adcbchn82qcl7ua1cm8.apps.googleusercontent.com",
]

# DynamoDB resource & table handles
dynamodb = boto3.resource("dynamodb", region_name="ap-south-1")
users_table = dynamodb.Table(USERS_TABLE)
doctors_table = dynamodb.Table(DOCTORS_TABLE)
auth_tokens_table = dynamodb.Table(AUTH_TOKENS_TABLE)
sessions_table = dynamodb.Table(SESSIONS_TABLE)
