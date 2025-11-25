import os
import boto3
# from dotenv import load_dotenv
# load_dotenv()

# AWS S3 client
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
S3_BUCKET = os.getenv("S3_BUCKET", "kokoro-doctor")
S3_FOLDER_PREFIX = "Medilocker/Users/"  # Folder prefix within the bucket
s3_client = boto3.client("s3", region_name=AWS_REGION)

# OpenAI API Key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
