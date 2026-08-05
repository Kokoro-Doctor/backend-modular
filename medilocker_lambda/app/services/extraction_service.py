"""
Extraction service - OCR and document extraction pipeline.

Orchestrates: OCR (Textract) → GPT structured extraction → patient context.
Delegates prescription synthesis to prescription_service.
Supports images and PDFs.
"""
import base64
import json
import os
import time
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import uuid4

from fastapi import HTTPException
from openai import OpenAI

# from app.config import OPENAI_API_KEY, PRESCRIPTION_MAX_DOCS, ALLOWED_EXTENSIONS
from app.config import (
    GROQ_API_KEY,
    GROQ_BASE_URL,
    PRESCRIPTION_MAX_DOCS,
    ALLOWED_EXTENSIONS,
    S3_BUCKET,
    S3_FOLDER_PREFIX,
    s3_client,
    CLAIM_VALIDATOR_MODEL,
)
from app.logger import get_logger
from app.models.structured_data import StructuredMedicalData
from app.services.ocr_service import extract_text_from_image, extract_text_from_pdf_s3
from app.services.context_service import (
    build_patient_context,
    documents_from_extracted_data,
)
from app.services import prescription_service

logger = get_logger(__name__)

_LOG_TRUNCATE = 1000
PDF_EXTENSIONS = {"pdf"}
PRESCRIPTION_ALLOWED_EXTENSIONS = ALLOWED_EXTENSIONS | PDF_EXTENSIONS

MAX_CHARS_PER_DOC = 2000  # keeps requests under gpt-oss-20b's TPM limit


def _is_pdf(filename: str) -> bool:
    _, ext = os.path.splitext(filename)
    return ext.lstrip(".").lower() in PDF_EXTENSIONS


def _upload_temp_to_s3(file_bytes: bytes, filename: str) -> str:
    """Upload a PDF to a temporary S3 location for Textract async OCR."""
    temp_id = uuid4().hex[:8]
    _, ext = os.path.splitext(filename)
    ext = ext.lstrip(".").lower() or "pdf"
    s3_key = f"{S3_FOLDER_PREFIX}_temp/prescription/{temp_id}/document.{ext}"

    s3_client.put_object(
        Bucket=S3_BUCKET,
        Key=s3_key,
        Body=file_bytes,
    )
    logger.info(f"[PRESCRIPTION] Uploaded temp file to s3://{S3_BUCKET}/{s3_key}")
    return s3_key


def _cleanup_temp_s3(s3_key: str) -> None:
    """Best-effort cleanup of temporary S3 object after OCR completes."""
    try:
        s3_client.delete_object(Bucket=S3_BUCKET, Key=s3_key)
        logger.info(f"[PRESCRIPTION] Cleaned up temp file s3://{S3_BUCKET}/{s3_key}")
    except Exception as e:
        logger.warning(f"[PRESCRIPTION] Failed to cleanup temp file {s3_key}: {e}")


def _truncate_for_log(text: str, max_len: int = _LOG_TRUNCATE) -> str:
    """Truncate text for log output, appending a marker when trimmed."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"... [truncated, {len(text)} total chars]"


def extract_structured_data_from_text(
    ocr_text: str,
    client: Optional[OpenAI] = None,
) -> Dict[str, Any]:
    """
    Extract structured medical data from OCR text using GPT.

    Args:
        ocr_text: Text extracted from OCR
        client: Optional OpenAI client. If None, creates one (with API key check).
                 Pass a client when calling in parallel to reuse a single instance.

    Returns:
        Structured JSON with medical data
    """
    if client is None:
        if not GROQ_API_KEY:
            raise HTTPException(status_code=500, detail="Groq API key not configured")
        client = OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL)

    if len(ocr_text) > MAX_CHARS_PER_DOC:
        ocr_text = ocr_text[:MAX_CHARS_PER_DOC] + "\n[... document truncated for length ...]"

    extraction_prompt = f"""
        You are a clinical medical data extraction system.

        Your task is to extract ONLY factual medical information explicitly present
        in the OCR text of a medical document.

        IMPORTANT RULES:

        1. DO NOT infer, assume, summarize beyond text, or add medical knowledge.
        2. Extract ONLY what is written.
        3. If a value is missing → return null.
        4. If a list has no items → return [].
        5. Output STRICT VALID JSON ONLY.
        6. Do NOT include explanations or extra keys.
        7. Dates must remain exactly as written (do not reformat).
        8. If unsure about classification → use "OTHER".

        --------------------------------------------------
        DOCUMENT CATEGORY (choose exactly one):

        PRESCRIPTION      → prescription slips, medication lists, doctor-prescribed medicines
        LAB_REPORT        → blood tests, pathology, lab results
        SCAN_REPORT       → X-ray, MRI, CT, ultrasound, radiology
        HEALTH_INSURANCE  → insurance policy, claim, approval
        HOSPITAL_RECORD   → discharge summary, consultation notes, hospital records
        OTHER             → anything else

        --------------------------------------------------
        EXTRACTION GOALS:

        Extract structured medical facts AND document context so the data
        can be used for:
        - AI prescription generation
        - longitudinal patient analysis
        - full medical case understanding

        --------------------------------------------------
        RETURN JSON IN EXACT STRUCTURE:

        {{
            "document_category": "OTHER",

            "document_metadata": {{
                "document_date": null,
                "doctor_name": null,
                "hospital_name": null,
                "department": null
            }},

            "patient_details": {{
                "name": null,
                "age": null,
                "gender": null
            }},

            "diagnoses": [],
            "symptoms": [],
            "medical_conditions": [],

            "medications": [
                {{
                "name": "",
                "dose": null,
                "frequency": null,
                "duration": null
                }}
            ],

            "tests": [],

            "lab_values": [
                {{
                "test_name": "",
                "value": null,
                "unit": null,
                "reference_range": null
                }}
            ],

            "medical_history": [],
            "clinical_context": [],

            "document_summary": ""
        }}

        --------------------------------------------------
        FIELD EXTRACTION GUIDELINES:

        document_category:
        Classify document using strongest textual evidence.

        document_metadata:
        Extract hospital/clinic name, consulting doctor, department,
        and document date if present anywhere in document headers or body.

        patient_details:
        Extract only explicitly stated demographic details.

        diagnoses:
        Confirmed diagnoses written by doctor.

        symptoms:
        Patient complaints or reported symptoms.

        medical_conditions:
        Chronic or ongoing conditions mentioned.

        medications:
        Extract prescribed medicines only.
        Do not invent dose/frequency.

        tests:
        Names of investigations ordered or reported.

        lab_values:
        Only structured measurable results with numbers.

        medical_history:
        Past illnesses, surgeries, or known history.

        clinical_context:
        Observations, clinical notes, impressions.

        document_summary:
        Write a SHORT factual summary (max 80 words)
        STRICTLY based on document content.
        No interpretation.

        --------------------------------------------------
        OCR TEXT STARTS BELOW:
        <<<OCR_TEXT>>>
        """.replace("<<<OCR_TEXT>>>", ocr_text)

    messages = [
        {
            "role": "system",
            "content": "You are a deterministic medical information extraction engine. You extract structured clinical facts from OCR text and return strict JSON matching the provided schema. Never hallucinate or infer missing information."
        },
        {
            "role": "user",
            "content": extraction_prompt
        }
    ]

    try:
        response = client.chat.completions.create(
            # model="gpt-4o",
            model=CLAIM_VALIDATOR_MODEL,
                        messages=messages,
            temperature=0.1,
            max_tokens=3000,
            response_format={"type": "json_object"},
        )

        response_content = response.choices[0].message.content.strip()
        parsed = json.loads(response_content)
        validated = StructuredMedicalData(**parsed)
        return validated.model_dump()
    except json.JSONDecodeError as e:
        logger.warning(f"[EXTRACT] Failed to parse extraction JSON: {str(e)}")
        return StructuredMedicalData().model_dump()
    except Exception as e:
        logger.error(f"[EXTRACT] Extraction failed: {type(e).__name__}: {str(e)}")
        raise


def _extract_ocr_from_file(file_data: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    Extract OCR text from a single image or PDF file.

    Returns {"filename", "text"} or None.
    """
    filename = file_data.get("filename", "unknown")
    content = file_data.get("content", "")

    _, ext = os.path.splitext(filename)
    ext = ext.lstrip(".").lower() or "bin"
    if ext not in PRESCRIPTION_ALLOWED_EXTENSIONS:
        logger.warning(f"[PRESCRIPTION] Skipping {filename} - unsupported type .{ext}")
        return None

    if not content:
        logger.warning(f"[PRESCRIPTION] Skipping file {filename} - no content")
        return None

    try:
        file_bytes = base64.b64decode(content)
        if _is_pdf(filename):
            s3_key = _upload_temp_to_s3(file_bytes, filename)
            try:
                ocr_text = extract_text_from_pdf_s3(S3_BUCKET, s3_key)
            finally:
                _cleanup_temp_s3(s3_key)
        else:
            ocr_text = extract_text_from_image(file_bytes)
        if ocr_text and ocr_text.strip():
            logger.info(f"[PRESCRIPTION] OCR extracted {len(ocr_text)} chars from {filename}")
            return {"filename": filename, "text": ocr_text}
        logger.warning(f"[PRESCRIPTION] No text extracted from {filename}")
    except Exception as e:
        logger.error(f"[PRESCRIPTION] OCR failed for {filename}: {e}")
    return None


def _parallel_extract_from_ocr_texts(
    ocr_texts: List[Dict[str, str]],
    client: OpenAI,
    log_prefix: str = "",
) -> List[Dict[str, Any]]:
    """Run parallel GPT extraction on OCR texts. Returns list of structured data."""
    logger.info(f"{log_prefix} Parallel extraction from {len(ocr_texts)} document(s)")

    def extract_from_doc(ocr_item: Dict[str, str]) -> Optional[Dict[str, Any]]:
        try:
            return extract_structured_data_from_text(ocr_item["text"], client)
        except Exception as e:
            logger.error(f"{log_prefix} Extraction failed for {ocr_item['filename']}: {e}")
            return None

    results = []
    with ThreadPoolExecutor(max_workers=min(len(ocr_texts), PRESCRIPTION_MAX_DOCS)) as executor:
        futures = {executor.submit(extract_from_doc, item): item for item in ocr_texts}
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)

    if results:
        logger.info(f"{log_prefix} Extracted data from {len(results)} document(s)")
    else:
        logger.warning(f"{log_prefix} No data extracted from any document")

    return results


def extract_structured_data_from_files(
    files: List[Dict[str, str]],
) -> Dict[str, Any]:
    """
    Extract prescription points and patient details from medical documents using OCR-first pipeline.

    Flow:
    1. Parallel OCR extraction (Textract) for each file
    2. Parallel GPT extraction per document
    3. Build patient context
    4. Final GPT synthesis (via prescription_service)

    Args:
        files: List of dicts with 'filename' and 'content' (base64 encoded)

    Returns:
        Dict with 'prescription' key containing plain text prescription points,
        and optionally 'patient_details' with Name, Age, Gender, Diagnosis if available
    """
    start_time = time.time()
    logger.info(f"[PRESCRIPTION] Processing {len(files)} file(s) with OCR-first pipeline")

    # if not OPENAI_API_KEY:
    #     raise HTTPException(status_code=500, detail="OpenAI API key not configured")
    if not GROQ_API_KEY:
        raise HTTPException(status_code=500, detail="Groq API key not configured")
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    # Validate all files are supported before starting OCR.
    for fd in files:
        filename = fd.get("filename", "unknown")
        _, ext = os.path.splitext(filename)
        ext = ext.lstrip(".").lower() or "bin"
        if ext not in PRESCRIPTION_ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"File type '.{ext}' is not allowed. "
                f"Accepted: {', '.join(sorted(PRESCRIPTION_ALLOWED_EXTENSIONS))}",
            )

    try:
        # client = OpenAI(api_key=OPENAI_API_KEY)
        client = OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL)

        # Step 1: Parallel OCR extraction
        logger.info(f"[PRESCRIPTION] Step 1: Parallel OCR extraction from {len(files)} file(s)")
        ocr_texts = []
        with ThreadPoolExecutor(max_workers=min(len(files), PRESCRIPTION_MAX_DOCS)) as executor:
            for future in as_completed(
                executor.submit(_extract_ocr_from_file, fd) for fd in files
            ):
                result = future.result()
                if result:
                    ocr_texts.append(result)

        if not ocr_texts:
            logger.warning("[PRESCRIPTION] No OCR text extracted from any file")
            return {"prescription": ""}

        for i, ocr_item in enumerate(ocr_texts):
            logger.info(
                f"[PRESCRIPTION] Stage 1 (OCR) doc {i + 1}/{len(ocr_texts)} "
                f"filename={ocr_item.get('filename')} len={len(ocr_item.get('text', ''))} chars. "
                f"Preview:\n{_truncate_for_log(ocr_item.get('text', ''))}"
            )

        # Step 2: Parallel GPT extraction
        extracted_data_list = _parallel_extract_from_ocr_texts(
            ocr_texts, client, log_prefix="[PRESCRIPTION]"
        )
        if not extracted_data_list:
            return {"prescription": ""}

        for i, data in enumerate(extracted_data_list):
            logger.info(
                f"[PRESCRIPTION] Stage 2 (GPT extract) doc {i + 1}/{len(extracted_data_list)}:\n"
                f"{_truncate_for_log(json.dumps(data, indent=2), max_len=2000)}"
            )

        # Step 3 & 4: Build context and synthesize
        documents = documents_from_extracted_data(extracted_data_list)
        patient_context = build_patient_context(documents)
        logger.info(
            f"[PRESCRIPTION] Stage 3 (patient context):\n"
            f"{_truncate_for_log(json.dumps(patient_context, indent=2), max_len=2500)}"
        )

        result = prescription_service.generate_prescription_from_context(patient_context)
        prescription_text = result.get("prescription", "")
        elapsed = time.time() - start_time
        logger.info(
            f"[PRESCRIPTION] Pipeline completed in {elapsed:.2f}s, "
            f"prescription len={len(prescription_text)} chars. "
            f"Preview:\n{_truncate_for_log(prescription_text, max_len=1500)}"
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {type(e).__name__}: {str(e)}")
        logger.exception("Full exception traceback:")
        raise HTTPException(status_code=500, detail=f"Failed to extract prescription: {str(e)}")
