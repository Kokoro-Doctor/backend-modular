import os
import boto3
# from dotenv import load_dotenv
# load_dotenv()

# AWS S3 client
S3_BUCKET = os.getenv("S3_BUCKET", "kokoro-medilocker")
s3_client = boto3.client("s3")
