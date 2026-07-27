import os
import boto3
# from dotenv import load_dotenv
# load_dotenv()

# AWS config
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

# AWS S3 client
S3_BUCKET = os.getenv("S3_BUCKET", "kokoro-doctor")
S3_FOLDER_PREFIX = "Medilocker/Users/"  # Folder prefix within the bucket
s3_client = boto3.client("s3", region_name=AWS_REGION)

# DynamoDB resource & table handles
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
DOCUMENTS_TABLE = os.getenv("DOCUMENTS_TABLE", "MedilockerDocuments")
documents_table = dynamodb.Table(DOCUMENTS_TABLE)
USERS_TABLE = os.getenv("USERS_TABLE", "Users")
users_table = dynamodb.Table(USERS_TABLE)

# # OpenAI API Key
# OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
# Groq API Key & base URL (kept for backward compatibility; no longer the
# default provider — see OPENROUTER_* below)
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# OpenRouter API Key & base URL — now the primary LLM provider for
# claim_validator_graph.py and the standalone extraction services, to avoid
# Groq's restrictive per-model TPM/TPD free-tier limits.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Prescription: max number of most recent documents to use when generating prescription
PRESCRIPTION_MAX_DOCS = int(os.getenv("PRESCRIPTION_MAX_DOCS", "10"))

# Upload validation
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "heic", "heif", "webp", "tiff", "tif", "bmp"}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB (decoded size)

HOSPITAL_API_KEY = os.getenv("HOSPITAL_API_KEY")

# ── Claim Validator config (Groq direct) ────────────────────────────────
# CLAIM_VALIDATOR_MODEL — high-frequency parsing/extraction. llama-3.1-8b
# has the highest TPD (500K) of Groq's Llama models, giving the most
# headroom for frequent extraction calls.
CLAIM_VALIDATOR_MODEL = os.getenv(
    "CLAIM_VALIDATOR_MODEL",
    "openai/gpt-oss-120b",
)

# CLAIM_REASONING_MODEL — lower-frequency reasoning/audit calls, where the
# stronger 70B model's quality matters more than its smaller 100K TPD.
CLAIM_REASONING_MODEL = os.getenv(
    "CLAIM_REASONING_MODEL",
    "llama-3.3-70b-versatile",
)

# Default policy baselines when company not detected in form
DEFAULT_GOVT_POLICY = "ayushman_bharat"
DEFAULT_PRIVATE_POLICY = "medi_assist"

# ── Async OCR queue ─────────────────────────────────────────────────
# SQS queue URL for background OCR jobs (used by upload/async endpoint).
OCR_QUEUE_URL = os.getenv("OCR_QUEUE_URL")
