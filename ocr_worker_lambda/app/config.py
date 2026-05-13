"""
Configuration for OCRWorkerLambda.

All values come from environment variables set in template.yaml.
No FastAPI dependency — this Lambda runs outside an HTTP context.
"""
import os
import boto3

# AWS
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

# S3
S3_BUCKET = os.getenv("S3_BUCKET", "kokoro-doctor")
S3_FOLDER_PREFIX = os.getenv("S3_FOLDER_PREFIX", "Medilocker/Users/")

s3_client = boto3.client("s3", region_name=AWS_REGION)

# DynamoDB
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
DOCUMENTS_TABLE = os.getenv("DOCUMENTS_TABLE", "MedilockerDocuments")
documents_table = dynamodb.Table(DOCUMENTS_TABLE)

# Allowed file extensions
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "heic", "heif", "webp", "tiff", "tif", "bmp"}
PDF_EXTENSIONS = {"pdf"}
ALL_ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS
