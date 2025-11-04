from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from app.utils import s3_utils
from app.models.schemas import UploadRequest, EmailRequest, FileRequest
from app.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/medilocker", tags=["Medilocker"])

@router.post("/upload")
async def upload_file(body: UploadRequest):
    try:
        s3_utils.upload_files(body.email, body.files)
        return JSONResponse(
            content={"message": "Files uploaded successfully"},
            headers={
                "Access-Control-Allow-Origin": "https://kokoro.doctor",
                "Access-Control-Allow-Credentials": "true"
            }
        )
    except Exception as e:
        logger.exception("Upload failed")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/fetch")
async def fetch_files(body: EmailRequest):
    try:
        files_info = s3_utils.fetch_files(body.email)
        if not files_info:
            return JSONResponse(
                content={"message": "No files found", "files": []},
                headers={
                    "Access-Control-Allow-Origin": "https://kokoro.doctor",
                    "Access-Control-Allow-Credentials": "true"
                }
            )
        return JSONResponse(
            content={"files": files_info},
            headers={
                "Access-Control-Allow-Origin": "https://kokoro.doctor",
                "Access-Control-Allow-Credentials": "true"
            }
        )
    except Exception as e:
        logger.exception("Fetch failed")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/download")
async def generate_download_link(body: FileRequest):
    try:
        url = s3_utils.generate_download_link(body.email, body.filename)
        return JSONResponse(
            content={"download_url": url},
            headers={
                "Access-Control-Allow-Origin": "https://kokoro.doctor",
                "Access-Control-Allow-Credentials": "true"
            }
        )
    except Exception as e:
        logger.exception("Presigned URL generation failed")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/delete")
async def delete_file(body: FileRequest):
    try:
        s3_utils.delete_file(body.email, body.filename)
        return JSONResponse(
            content={"message": "File deleted successfully"},
            headers={
                "Access-Control-Allow-Origin": "https://kokoro.doctor",
                "Access-Control-Allow-Credentials": "true"
            }
        )
    except Exception as e:
        logger.exception("Deletion failed")
        raise HTTPException(status_code=500, detail=str(e))
