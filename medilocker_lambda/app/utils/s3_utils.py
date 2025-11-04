import base64
from fastapi import HTTPException
from app.config import s3_client, S3_BUCKET
from app.logger import get_logger

logger = get_logger(__name__)

def list_user_files(email: str):
    response = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix=f"{email}/")
    return response.get("Contents", [])

def upload_files(email: str, files):
    existing = {obj["Key"].split("/")[-1] for obj in list_user_files(email)}
    for file in files:
        if file.filename in existing:
            raise HTTPException(status_code=409, detail="File with the same name already exists")
        try:
            file_binary = base64.b64decode(file.content)
        except base64.binascii.Error:
            raise HTTPException(status_code=400, detail="Invalid base64 encoding")

        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=f"{email}/{file.filename}",
            Body=file_binary,
            Metadata={k: str(v) for k, v in file.metadata.items()}
        )

def fetch_files(email: str):
    files = list_user_files(email)
    if not files:
        return []
    files_info = []
    for obj in files:
        key = obj["Key"]
        filename = key.split(f"{email}/", 1)[1]
        try:
            head_response = s3_client.head_object(Bucket=S3_BUCKET, Key=key)
            metadata = head_response.get("Metadata", {})
        except Exception:
            metadata = {}
        files_info.append({"filename": filename, "metadata": metadata})
    return files_info

def generate_download_link(email: str, filename: str):
    file_key = f"{email}/{filename}"
    return s3_client.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": S3_BUCKET, "Key": file_key},
        ExpiresIn=3600
    )

def delete_file(email: str, filename: str):
    file_key = f"{email}/{filename}"
    try:
        s3_client.head_object(Bucket=S3_BUCKET, Key=file_key)
    except s3_client.exceptions.ClientError as e:
        if e.response["Error"]["Code"] == "404":
            raise HTTPException(status_code=404, detail="File not found")
        raise
    s3_client.delete_object(Bucket=S3_BUCKET, Key=file_key)
