"""
Medilocker router - thin wrapper around file and prescription services.

Architecture:
  DynamoDB is the source of truth. Prescription flow queries DynamoDB for
  documents with ocr_status == COMPLETED, fetches pre-computed OCR from S3.
"""
import time
import json
from typing import Optional

from fastapi import APIRouter, Body, File, HTTPException, Path, Query, UploadFile
from openai import OpenAI

from app.services import file_service
from app.services import prescription_service
from app.services import extraction_service
from app.services import document_db_service
from app.services import insurance_extraction_service
from app.services import discharge_extraction_service
from app.services.user_diagnosis_service import save_diagnosis_fields_for_user
from app.models.schemas import UploadRequest, ExtractionRequest, ClinicalQueryRequest, SavePrescriptionRequest
from app.services.context_service import build_patient_context
from app.services.clinical_query_service import answer_clinical_query
from app.logger import get_logger
# from app.config import PRESCRIPTION_MAX_DOCS, OPENAI_API_KEY
from app.config import PRESCRIPTION_MAX_DOCS, GROQ_API_KEY, GROQ_BASE_URL
from typing import Optional

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
async def fetch_files(
    user_id: str = Path(..., description="User ID"),
    category: Optional[str] = Query(None, description="Filter by document category (e.g. LAB_REPORT, SCAN_REPORT)"),
):
    """
    List all files for a user. Optionally filter by document_category.
    """
    try:
        files_info = file_service.fetch_files(user_id, category)
        if not files_info:
            return {"message": "No files found", "files": []}
        return {"files": files_info}
    except Exception as e:
        logger.exception("Fetch failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/users/{user_id}/files/{file_id}/download")
async def generate_download_link(
    user_id: str = Path(..., description="User ID"),
    file_id: str = Path(..., description="file_id from list response")
):
    """
    Generate presigned download URL. Use file_id from list response.
    """
    try:
        url = file_service.generate_download_link(user_id, file_id)
        return {"download_url": url}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Presigned URL generation failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/users/{user_id}/files/{file_id}")
async def delete_file(
    user_id: str = Path(..., description="User ID"),
    file_id: str = Path(..., description="file_id from list response")
):
    """
    Delete a file. Use file_id from list response.
    """
    try:
        file_service.delete_file(user_id, file_id)
        return {"message": "File deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Deletion failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/users/{user_id}/prescription")
async def generate_prescription_from_s3_files(user_id: str = Path(..., description="User ID")):
    """
    Generate prescription for top N most recently uploaded documents.

    Reads pre-extracted structured_data from DynamoDB (written at upload
    time), builds a chronological patient context, and runs a single GPT
    synthesis call.  No OCR and no per-document extraction happens here.
    """
    start_time = time.time()

    try:
        logger.info(f"[PRESCRIPTION] Request received for user_id: {user_id}")

        db_docs = document_db_service.get_latest_documents(
            user_id, limit=PRESCRIPTION_MAX_DOCS
        )

        valid_documents = [
            doc for doc in db_docs
            if doc.get("ocr_status") == "COMPLETED"
            and doc.get("structured_status") == "COMPLETED"
        ]

        if not valid_documents:
            elapsed_time = time.time() - start_time
            logger.info(
                f"[PRESCRIPTION] No fully-processed documents for user "
                f"{user_id}, returning empty ({elapsed_time:.2f}s)"
            )
            return {"prescription": ""}

        patient_context = build_patient_context(valid_documents)

        if not patient_context.get("document_history"):
            elapsed_time = time.time() - start_time
            logger.info(
                f"[PRESCRIPTION] No usable structured data for user "
                f"{user_id} ({elapsed_time:.2f}s)"
            )
            return {"prescription": ""}

        result = prescription_service.generate_prescription_from_context(
            patient_context
        )

        elapsed_time = time.time() - start_time
        logger.info(f"[PRESCRIPTION] Completed in {elapsed_time:.2f}s")
        return result

    except HTTPException as http_exc:
        elapsed_time = time.time() - start_time
        logger.error(
            f"[PRESCRIPTION] HTTP Exception after {elapsed_time:.2f}s: "
            f"status={http_exc.status_code}, detail={http_exc.detail}"
        )
        raise
    except Exception as e:
        elapsed_time = time.time() - start_time
        logger.error(
            f"[PRESCRIPTION] Unexpected error after {elapsed_time:.2f}s: "
            f"{type(e).__name__}: {str(e)}"
        )
        logger.exception("Full exception traceback:")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/users/{user_id}/prescription/save")
async def save_prescription(
    user_id: str = Path(..., description="Patient's user ID (Medilocker owner)"),
    payload: SavePrescriptionRequest = Body(...),
):
    """
    Save an approved prescription to the patient's Medilocker.

    Stores the prescription as a document that appears in file listings and
    can be downloaded via the existing download endpoint. Document category
    is set to PRESCRIPTION.
    """
    try:
        result = file_service.save_prescription_to_medilocker(
            user_id=user_id,
            prescription_pdf_base64=payload.prescription_pdf,
        )
        return {
            "message": "Prescription saved to Medilocker successfully",
            "file_id": result["file_id"],
            "filename": result["filename"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Save prescription failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/users/{user_id}/clinical-query")
async def clinical_query(
    user_id: str = Path(..., description="User ID"),
    payload: ClinicalQueryRequest = Body(...),
):
    """
    Doctor asks question about patient's stored medical records.
    """
    # if not OPENAI_API_KEY:
    #     raise HTTPException(status_code=500, detail="OpenAI API key not configured")
    if not GROQ_API_KEY:
        raise HTTPException(status_code=500, detail="Groq API key not configured")

    documents = document_db_service.get_latest_documents(
        user_id, limit=PRESCRIPTION_MAX_DOCS
    )

    if not documents:
        return {"answer": "No medical documents available for this patient."}

    valid_documents = [
        d for d in documents
        if d.get("ocr_status") == "COMPLETED"
        and d.get("structured_status") == "COMPLETED"
    ]

    if not valid_documents:
        return {"answer": "No processed medical data available."}

    patient_context = build_patient_context(valid_documents)

    # openai_client = OpenAI(api_key=OPENAI_API_KEY)
    openai_client = OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL)
    result = answer_clinical_query(
        patient_context=patient_context,
        question=payload.question,
        client=openai_client,
    )
    return result


@router.post("/users/{user_id}/insurance/autofill-stored")
async def autofill_insurance_from_stored_docs(
    user_id: str = Path(..., description=(
        "User ID — must have INSURANCE_POLICY, HOSPITAL_BILL, and PRESCRIPTION "
        "documents already uploaded and OCR-processed in MedilockerDocuments"
    )),
):
    """
    Pipeline 2 — Autofill insurance claim form from pre-OCR'd stored documents.

    Fetches INSURANCE_POLICY, HOSPITAL_BILL, and PRESCRIPTION documents already
    stored for this user, reads their pre-computed OCR texts from S3, and runs
    the autofill pipeline (multi_doc_extractor → claim_form_filler) without
    re-running Textract.

    Returns the same autofill_result shape as POST /medilocker/insurance/analyze.

    Error codes:
      404 — one or more of the 3 required documents have not been uploaded yet
      409 — documents exist but OCR is still in progress (retry later)
      500 — S3 read failure or unexpected error
    """
    start_time = time.time()
    try:
        from app.services.claim_validator_graph import autofill_from_stored_docs

        logger.info(f"[INSURANCE_STORED] Request received for user_id={user_id}")
        result = autofill_from_stored_docs(user_id)

        error_code = result.get("error_code")
        if error_code == "missing_docs":
            raise HTTPException(
                status_code=404,
                detail=result.get("error", "Missing required claim documents"),
            )
        if error_code == "ocr_pending":
            raise HTTPException(
                status_code=409,
                detail=result.get("error", "OCR still in progress — retry shortly"),
            )
        if error_code == "s3_read_failed":
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "Failed to read stored OCR text"),
            )

        if not result.get("error"):
            try:
                save_diagnosis_fields_for_user(user_id, result)
            except Exception as exc:
                logger.exception(
                    "[INSURANCE_STORED] Diagnosis persistence failed for user_id=%s: %s",
                    user_id,
                    exc,
                )

        elapsed = time.time() - start_time
        logger.info(
            f"[INSURANCE_STORED] Completed in {elapsed:.2f}s for user_id={user_id}"
        )
        return result

    except HTTPException:
        raise
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(
            f"[INSURANCE_STORED] Unexpected error after {elapsed:.2f}s: "
            f"{type(e).__name__}: {str(e)}"
        )
        logger.exception("Full exception traceback:")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/insurance/analyze")
async def analyze_insurance_data(
    claim_form: Optional[UploadFile] = File(None, description="Insurance claim form (optional — if not provided, autofill mode activates)"),
    hospital_bill: Optional[UploadFile] = File(None, description="Itemized hospital bill / discharge summary"),
    doctor_prescription: Optional[UploadFile] = File(None, description="Signed prescription from doctor"),
    insurance_policy: Optional[UploadFile] = File(None, description="Insurance policy card / policy document"),
):
    """
    Analyze insurance claim with cross-document verification.
    
    claim_form is mandatory. Other documents are optional but improve
    audit quality through cross-verification.
    """
    start_time = time.time()
    
    try:
        documents = {}
        filenames = {}

        if claim_form is not None:
            documents["claim_form"] = await claim_form.read()
            filenames["claim_form"] = claim_form.filename or "claim_form.pdf"

        if hospital_bill is not None:
            documents["hospital_bill"] = await hospital_bill.read()
            filenames["hospital_bill"] = hospital_bill.filename or "hospital_bill.pdf"

        if doctor_prescription is not None:
            documents["doctor_prescription"] = await doctor_prescription.read()
            filenames["doctor_prescription"] = doctor_prescription.filename or "prescription.pdf"

        if insurance_policy is not None:
            documents["insurance_policy"] = await insurance_policy.read()
            filenames["insurance_policy"] = insurance_policy.filename or "insurance_policy.pdf"

        if not documents:
            raise HTTPException(status_code=400, detail="At least one document must be uploaded")

        total_size = sum(len(b) for b in documents.values())
        logger.info(
            f"[INSURANCE] Request: {len(documents)} file(s), "
            f"{total_size} bytes, types: {list(documents.keys())}, "
            f"flow: {'audit' if 'claim_form' in documents else 'autofill'}"
        )

        from app.services.claim_validator_graph import validate_claim
        result = validate_claim(documents=documents, filenames=filenames)

        elapsed_time = time.time() - start_time
        logger.info(f"[INSURANCE] Completed in {elapsed_time:.2f}s")
        return result
    
    except HTTPException:
        raise
    except Exception as e:
        elapsed_time = time.time() - start_time
        logger.error(
            f"[INSURANCE] Unexpected error after {elapsed_time:.2f}s: "
            f"{type(e).__name__}: {str(e)}"
        )
        logger.exception("Full exception traceback:")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/insurance/analyze/stream")
async def analyze_insurance_data_stream(
    file: UploadFile = File(..., description="Insurance document (image or PDF)"),
):
    """
    Analyze an insurance document with SSE streaming.
    Streams CoT thinking and node results as they complete.
    """
    from sse_starlette.sse import EventSourceResponse
    from app.services.claim_validator_graph import validate_claim_streaming

    file_bytes = await file.read()
    filename = file.filename or "unknown"
    logger.info(
        f"[INSURANCE_STREAM] Request received file={filename} "
        f"({len(file_bytes)} bytes)"
    )

    async def event_generator():
        async for payload in validate_claim_streaming(file_bytes, filename):
            yield {
                "event": payload.get("node", "update"),
                "data": json.dumps(payload),
            }

    return EventSourceResponse(event_generator())


@router.post("/discharge/analyze")
async def analyze_discharge_data(
    file: UploadFile = File(..., description="Discharge summary document (image or PDF)"),
):
    """
    Analyze a discharge summary document.

    Accepts a single file via multipart/form-data (image or PDF), runs OCR,
    extracts structured data, and returns analysis. No user id required (stateless).
    """
    start_time = time.time()

    try:
        file_bytes = await file.read()
        filename = file.filename or "unknown"
        logger.info(
            f"[DISCHARGE] Request received file={filename} ({len(file_bytes)} bytes)"
        )

        result = discharge_extraction_service.extract_discharge_data_from_file(
            file_bytes=file_bytes,
            filename=filename,
        )

        elapsed_time = time.time() - start_time
        logger.info(f"[DISCHARGE] Endpoint completed in {elapsed_time:.2f}s")
        return result

    except HTTPException:
        raise
    except Exception as e:
        elapsed_time = time.time() - start_time
        logger.error(
            f"[DISCHARGE] Unexpected error after {elapsed_time:.2f}s: "
            f"{type(e).__name__}: {str(e)}"
        )
        logger.exception("Full exception traceback:")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/prescription")
async def extract_structured_data(body: ExtractionRequest):
    """
    Extract prescription points directly from uploaded files using GPT-4 Vision.
    Files should contain base64-encoded content.
    """
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
        
        result = extraction_service.extract_structured_data_from_files(files)
        
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


# ---------------------------------------------------------------------------
# Async upload — store immediately, OCR runs in OCRWorkerLambda
# ---------------------------------------------------------------------------

@router.post("/upload/async", status_code=202)
async def upload_file_async(body: UploadRequest):
    """
    Async file upload — accepts images and PDFs.

    Stores the file in S3, creates a DynamoDB record with
    ocr_status=PENDING and upload_mode=ASYNC, then dispatches
    an SQS message to the OCRWorkerLambda. Returns 202 immediately.

    Poll GET /medilocker/users/{user_id}/files/{file_id}/status to
    track when OCR and structured extraction complete.
    """
    try:
        results = file_service.upload_files_async(body.user_id, body.files)
        return {
            "message": "Files accepted for background processing",
            "files": results,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Async upload failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Status check — poll OCR / extraction progress for a single file
# ---------------------------------------------------------------------------

@router.get("/users/{user_id}/files/{file_id}/status")
async def get_file_status(
    user_id: str = Path(..., description="User ID"),
    file_id: str = Path(..., description="file_id returned from upload"),
):
    """
    Return the OCR and structured extraction status for a single file.

    Response fields:
      file_id          — the queried file
      ocr_status       — PENDING | COMPLETED | FAILED
      structured_status — PENDING | COMPLETED | FAILED
      upload_mode      — LIVE | ASYNC
      updated_at       — ISO timestamp of last status change
    """
    doc = document_db_service.get_document_by_file_id(user_id, file_id)
    if not doc:
        raise HTTPException(status_code=404, detail="File not found")

    return {
        "file_id": file_id,
        "filename": doc.get("filename"),
        "ocr_status": doc.get("ocr_status", "PENDING"),
        "structured_status": doc.get("structured_status", "PENDING"),
        "upload_mode": doc.get("upload_mode", "LIVE"),
        "document_category": doc.get("document_category"),
        "updated_at": doc.get("updated_at"),
    }
