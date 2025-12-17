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
    Extract structured prescription data from uploaded files using GPT-4 Vision.
    Files should contain base64-encoded content.
    """
    import time
    start_time = time.time()
    
    try:
        logger.info("=" * 80)
        logger.info("📥 [EXTRACT] Request received for structured data extraction")
        logger.info(f"📥 [EXTRACT] Number of files: {len(body.files)}")
        logger.info(f"📥 [EXTRACT] Has frontend_patient_details: {body.frontend_patient_details is not None}")
        
        # Log file information (without exposing full base64 content)
        for idx, file in enumerate(body.files):
            content_length = len(file.content) if file.content else 0
            content_preview = file.content[:50] + "..." if file.content and len(file.content) > 50 else (file.content or "empty")
            logger.info(f"📥 [EXTRACT] File {idx + 1}: filename='{file.filename}', content_length={content_length} bytes, preview='{content_preview}'")
        
        # Convert FileUploadModel to dict format expected by extraction function
        files = [
            {
                "filename": file.filename,
                "content": file.content
            }
            for file in body.files
        ]
        
        logger.info(f"🔄 [EXTRACT] Starting extraction process for {len(files)} file(s)")
        
        result = prescription_service.extract_structured_data_from_files(
            files,
            body.frontend_patient_details
        )
        
        elapsed_time = time.time() - start_time
        logger.info(f"✅ [EXTRACT] Extraction completed successfully in {elapsed_time:.2f} seconds")
        logger.info(f"✅ [EXTRACT] Result summary: has_patient_details={bool(result.get('patient_details'))}, has_prescription_report={bool(result.get('prescription_report'))}")
        logger.info(f"✅ [EXTRACT] Prescription report length: {len(result.get('prescription_report', ''))} characters")
        logger.info("=" * 80)
        
        return result
    except HTTPException as http_exc:
        elapsed_time = time.time() - start_time
        logger.error(f"❌ [EXTRACT] HTTP Exception after {elapsed_time:.2f} seconds: status={http_exc.status_code}, detail={http_exc.detail}")
        logger.info("=" * 80)
        raise
    except Exception as e:
        elapsed_time = time.time() - start_time
        logger.error(f"❌ [EXTRACT] Unexpected error after {elapsed_time:.2f} seconds: {type(e).__name__}: {str(e)}")
        logger.exception("❌ [EXTRACT] Full exception traceback:")
        logger.info("=" * 80)
        raise HTTPException(status_code=500, detail=str(e))
