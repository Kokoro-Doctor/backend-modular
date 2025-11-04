import base64
import mimetypes
import urllib.parse
from app.logger import logger
from app.config import S3, S3_BUCKET

def upload_doc_to_s3(email, doc_type, filename, base64_content):
    try:
        key = f"doctors/{email}/{doc_type}/{urllib.parse.quote(filename)}"
        file_bytes = base64.b64decode(base64_content)
        content_type, _ = mimetypes.guess_type(filename)
        content_type = content_type or "application/octet-stream"

        logger.info(f"Uploading {key} ({content_type})")
        S3.put_object(Bucket=S3_BUCKET, Key=key, Body=file_bytes, ContentType=content_type)
        return f"https://{S3_BUCKET}.s3.amazonaws.com/{key}"
    except Exception as e:
        logger.error(f"S3 upload error for {filename}: {e}")
        raise

def generate_presigned_url(key: str, expires_in=3600):
    try:
        return S3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": S3_BUCKET, "Key": key},
            ExpiresIn=expires_in,
        )
    except Exception as e:
        logger.error(f"Presigned URL error: {e}")
        return None
