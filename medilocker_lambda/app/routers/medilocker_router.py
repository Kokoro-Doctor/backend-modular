"""
Medilocker router - thin wrapper around file and prescription services.
"""
from fastapi import APIRouter, HTTPException, Query, Path
from app.services import file_service
from app.services import prescription_service
from app.models.schemas import UploadRequest, ExtractionRequest
from app.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/medilocker", tags=["Medilocker"])


@router.post("/upload")
async def upload_file(body: UploadRequest):
    try:
        file_service.upload_files(body.user_id, body.files)
        return {"message": "Files uploaded successfully"}
    except Exception as e:
        logger.exception("Upload failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/users/{user_id}/files")
async def fetch_files(user_id: str = Path(..., description="User ID")):
    """
    List all files for a user.
    Resource identifier (user_id) is in the path.
    """
    try:
        files_info = file_service.fetch_files(user_id)
        if not files_info:
            return {"message": "No files found", "files": []}
        return {"files": files_info}
    except Exception as e:
        logger.exception("Fetch failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/users/{user_id}/files/{filename:path}/download")
async def generate_download_link(
    user_id: str = Path(..., description="User ID"),
    filename: str = Path(..., description="Filename")
):
    """
    Generate presigned download URL for a file.
    Resource identifiers (user_id, filename) are in the path.
    """
    try:
        url = file_service.generate_download_link(user_id, filename)
        return {"download_url": url}
    except Exception as e:
        logger.exception("Presigned URL generation failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/users/{user_id}/files/{filename:path}")
async def delete_file(
    user_id: str = Path(..., description="User ID"),
    filename: str = Path(..., description="Filename")
):
    """
    Delete a file from user's medilocker.
    Resource identifiers (user_id, filename) are in the path.
    """
    try:
        file_service.delete_file(user_id, filename)
        return {"message": "File deleted successfully"}
    except Exception as e:
        logger.exception("Deletion failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/extract-structured-data")
async def extract_structured_data(body: ExtractionRequest):
    """
    Extract prescription points directly from uploaded files using GPT-4 Vision.
    Files should contain base64-encoded content.
    """
    import time
    start_time = time.time()
    
    try:
        logger.info(f"[EXTRACT] Request received for prescription extraction")
        logger.info(f"[EXTRACT] Number of files: {len(body.files)}")
        
        # Convert FileUploadModel to dict format expected by extraction function
        files = [
            {
                "filename": file.filename,
                "content": file.content
            }
            for file in body.files
        ]
        
        result = prescription_service.extract_structured_data_from_files(files)
        
        elapsed_time = time.time() - start_time
        logger.info(f"[EXTRACT] Extraction completed in {elapsed_time:.2f}s")
        logger.info(f"[EXTRACT] Prescription length: {len(result.get('prescription', ''))} characters")
        
        return result
    except HTTPException as http_exc:
        elapsed_time = time.time() - start_time
        logger.error(f"[EXTRACT] HTTP Exception after {elapsed_time:.2f}s: status={http_exc.status_code}, detail={http_exc.detail}")
        raise
    except Exception as e:
        elapsed_time = time.time() - start_time
        logger.error(f"[EXTRACT] Unexpected error after {elapsed_time:.2f}s: {type(e).__name__}: {str(e)}")
        logger.exception("Full exception traceback:")
        raise HTTPException(status_code=500, detail=str(e))
