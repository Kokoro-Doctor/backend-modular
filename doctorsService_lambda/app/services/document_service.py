"""
Document service - handles S3 document operations for doctors.
"""
import base64
import mimetypes
import urllib.parse
from app.logger import logger
from app.config import S3, S3_BUCKET, S3_FOLDER_PREFIX


def upload_doc_to_s3(doctor_id: str, doc_type: str, filename: str, base64_content: str) -> str:
    """Upload a document to S3 and return the URL"""
    try:
        key = f"{S3_FOLDER_PREFIX}doctors/{doctor_id}/{doc_type}/{urllib.parse.quote(filename)}"
        file_bytes = base64.b64decode(base64_content)
        content_type, _ = mimetypes.guess_type(filename)
        content_type = content_type or "application/octet-stream"

        logger.info(f"Uploading {key} ({content_type})")
        S3.put_object(Bucket=S3_BUCKET, Key=key, Body=file_bytes, ContentType=content_type)
        return f"https://{S3_BUCKET}.s3.amazonaws.com/{key}"
    except Exception as e:
        logger.error(f"S3 upload error for {filename}: {e}")
        raise


def generate_presigned_url(key: str, expires_in: int = 3600) -> str:
    """Generate a presigned URL for a document"""
    try:
        if not key.startswith(S3_FOLDER_PREFIX):
            key = f"{S3_FOLDER_PREFIX}{key}"
        return S3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": S3_BUCKET, "Key": key},
            ExpiresIn=expires_in,
        )
    except Exception as e:
        logger.error(f"Presigned URL error: {e}")
        raise

