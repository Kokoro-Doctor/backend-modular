"""
File service - handles S3 file operations for medilocker.
"""
import base64
from fastapi import HTTPException
from botocore.exceptions import ClientError
from app.config import s3_client, S3_BUCKET, S3_FOLDER_PREFIX
from app.logger import get_logger

logger = get_logger(__name__)


def list_user_files(user_id: str):
    """List all files for a user"""
    prefix = f"{S3_FOLDER_PREFIX}{user_id}/"
    logger.info(f"Listing files with bucket: {S3_BUCKET}, prefix: {prefix}")
    try:
        response = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)
        return response.get("Contents", [])
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "NoSuchBucket":
            logger.error(f"Bucket {S3_BUCKET} does not exist")
            raise HTTPException(status_code=500, detail=f"S3 bucket {S3_BUCKET} does not exist")
        elif error_code == "404":
            logger.info(f"No files found for user {user_id} (folder doesn't exist yet)")
            return []
        else:
            logger.error(f"Error listing files: {e}")
            raise HTTPException(status_code=500, detail=f"Error accessing S3: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error listing files: {e}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


def upload_files(user_id: str, files):
    """Upload files for a user"""
    existing = {obj["Key"].split("/")[-1] for obj in list_user_files(user_id)}
    for file in files:
        if file.filename in existing:
            raise HTTPException(status_code=409, detail="File with the same name already exists")
        try:
            file_binary = base64.b64decode(file.content)
        except base64.binascii.Error:
            raise HTTPException(status_code=400, detail="Invalid base64 encoding")

        key = f"{S3_FOLDER_PREFIX}{user_id}/{file.filename}"
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=file_binary,
            Metadata={k: str(v) for k, v in file.metadata.items()}
        )


def fetch_files(user_id: str):
    """Fetch file metadata for a user"""
    files = list_user_files(user_id)
    if not files:
        return []
    files_info = []
    for obj in files:
        key = obj["Key"]
        prefix_to_remove = f"{S3_FOLDER_PREFIX}{user_id}/"
        filename = key.replace(prefix_to_remove, "")
        try:
            head_response = s3_client.head_object(Bucket=S3_BUCKET, Key=key)
            metadata = head_response.get("Metadata", {})
        except Exception:
            metadata = {}
        files_info.append({"filename": filename, "metadata": metadata})
    return files_info


def generate_download_link(user_id: str, filename: str):
    """Generate a presigned download URL for a file"""
    file_key = f"{S3_FOLDER_PREFIX}{user_id}/{filename}"
    return s3_client.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": S3_BUCKET, "Key": file_key},
        ExpiresIn=3600
    )


def delete_file(user_id: str, filename: str):
    """Delete a file"""
    file_key = f"{S3_FOLDER_PREFIX}{user_id}/{filename}"
    try:
        s3_client.head_object(Bucket=S3_BUCKET, Key=file_key)
    except s3_client.exceptions.ClientError as e:
        if e.response["Error"]["Code"] == "404":
            raise HTTPException(status_code=404, detail="File not found")
        raise
    s3_client.delete_object(Bucket=S3_BUCKET, Key=file_key)

