"""
Discharge summary extraction service - OCR and structured extraction for discharge documents.

Supports images (Textract detect_document_text) and PDFs (Textract analyze_document via S3).

Independent from insurance_extraction_service.py and medical extraction_service.py.
"""
import json
import os
import time
from typing import Any, Dict, Optional
from uuid import uuid4

from openai import OpenAI

from app.config import (
    ALLOWED_EXTENSIONS,
    GROQ_API_KEY,
    GROQ_BASE_URL,
    S3_BUCKET,
    S3_FOLDER_PREFIX,
    s3_client,
    CLAIM_VALIDATOR_MODEL,
)
from app.logger import get_logger
from app.services.ocr_service import extract_text_from_image, extract_text_from_pdf_s3

logger = get_logger(__name__)

PDF_EXTENSIONS = {"pdf"}
IMAGE_EXTENSIONS = ALLOWED_EXTENSIONS
DISCHARGE_ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS

MAX_CHARS_PER_DOC = 2000  # keeps requests under gpt-oss-20b's TPM limit


def _is_pdf(filename: str) -> bool:
    _, ext = os.path.splitext(filename)
    return ext.lstrip(".").lower() in PDF_EXTENSIONS


def _upload_temp_to_s3(file_bytes: bytes, filename: str) -> str:
    """Upload file to a temporary S3 location for Textract processing."""
    temp_id = uuid4().hex[:8]
    _, ext = os.path.splitext(filename)
    ext = ext.lstrip(".").lower() or "pdf"
    s3_key = f"{S3_FOLDER_PREFIX}_temp/discharge/{temp_id}/document.{ext}"

    s3_client.put_object(
        Bucket=S3_BUCKET,
        Key=s3_key,
        Body=file_bytes,
    )
    logger.info(f"[DISCHARGE] Uploaded temp file to s3://{S3_BUCKET}/{s3_key}")
    return s3_key


def _cleanup_temp_s3(s3_key: str) -> None:
    """Best-effort cleanup of temporary S3 object after processing."""
    try:
        s3_client.delete_object(Bucket=S3_BUCKET, Key=s3_key)
        logger.info(f"[DISCHARGE] Cleaned up temp file s3://{S3_BUCKET}/{s3_key}")
    except Exception as e:
        logger.warning(f"[DISCHARGE] Failed to cleanup temp file {s3_key}: {e}")


def get_empty_discharge_schema() -> Dict[str, Any]:
    """Return empty discharge summary schema for fallback on parse failure."""
    return {
        "document_category": "DISCHARGE_SUMMARY",
        "patient_details": {
            "name": None,
            "age": None,
            "gender": None,
            "patient_id": None,
        },
        "admission_details": {
            "admission_date": None,
            "discharge_date": None,
            "length_of_stay": None,
        },
        "clinical_details": {
            "chief_complaint": None,
            "diagnosis": [],
            "procedures": [],
            "hospital_course": None,
        },
        "vitals_at_discharge": {
            "blood_pressure": None,
            "pulse": None,
            "temperature": None,
            "respiratory_rate": None,
            "other": None,
        },
        "medications_at_discharge": [],
        "follow_up": {
            "instructions": None,
            "next_visit_date": None,
            "referrals": [],
        },
        "treating_team": {
            "primary_physician": None,
            "department": None,
            "hospital_name": None,
        },
        "document_metadata": {
            "document_date": None,
        },
        "document_summary": "",
    }


def extract_discharge_structured_data_from_text(
    ocr_text: str,
    client: Optional[OpenAI] = None,
) -> Dict[str, Any]:
    """
    Extract structured discharge summary data from OCR text using the LLM.

    Uses discharge-specific schema and prompt.
    """
    if client is None:
        if not GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY not configured")
        client = OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL)

    prompt = """You are a hospital discharge summary data extraction system.

        Extract ONLY explicitly present information from the document.

        STRICT RULES:
        1. Do NOT infer or assume anything
        2. Missing values → null; empty lists → []
        3. Return STRICT JSON ONLY
        4. Do NOT add extra fields beyond the schema
        5. For "diagnosis" and "procedures", use arrays of strings (each item one line/condition)
        6. For "medications_at_discharge", use an array of objects with keys:
           "name", "dose", "frequency", "duration", "instructions" (use null if a sub-field is missing)

        --------------------------------------------------

        RETURN JSON IN EXACT STRUCTURE:

        {
        "document_category": "DISCHARGE_SUMMARY",

        "patient_details": {
            "name": null,
            "age": null,
            "gender": null,
            "patient_id": null
        },

        "admission_details": {
            "admission_date": null,
            "discharge_date": null,
            "length_of_stay": null
        },

        "clinical_details": {
            "chief_complaint": null,
            "diagnosis": [],
            "procedures": [],
            "hospital_course": null
        },

        "vitals_at_discharge": {
            "blood_pressure": null,
            "pulse": null,
            "temperature": null,
            "respiratory_rate": null,
            "other": null
        },

        "medications_at_discharge": [],

        "follow_up": {
            "instructions": null,
            "next_visit_date": null,
            "referrals": []
        },

        "treating_team": {
            "primary_physician": null,
            "department": null,
            "hospital_name": null
        },

        "document_metadata": {
            "document_date": null
        },

        "document_summary": ""
        }

        --------------------------------------------------

        OCR TEXT:
        <<<OCR_TEXT>>>
        """

    if len(ocr_text) > MAX_CHARS_PER_DOC:
        ocr_text = ocr_text[:MAX_CHARS_PER_DOC] + "\n[... document truncated for length ...]"

    messages = [
        {
            "role": "system",
            "content": (
                "You extract structured discharge summary data from OCR text. "
                "Return strict JSON only."
            ),
        },
        {
            "role": "user",
            "content": prompt.replace("<<<OCR_TEXT>>>", ocr_text),
        },
    ]

    try:
        response = client.chat.completions.create(
            model=CLAIM_VALIDATOR_MODEL,
            reasoning_effort="low",
            messages=messages,
            temperature=0.1,
            max_tokens=3000,
            response_format={"type": "json_object"},
        )
        response_content = response.choices[0].message.content.strip()
        parsed = json.loads(response_content)
        return parsed
    except json.JSONDecodeError as e:
        logger.warning(f"[DISCHARGE] Failed to parse extraction JSON: {e}")
        return get_empty_discharge_schema()
    except Exception as e:
        logger.error(f"[DISCHARGE] Extraction failed: {type(e).__name__}: {e}")
        return get_empty_discharge_schema()


def get_empty_discharge_analysis_schema() -> Dict[str, Any]:
    """Return empty analysis schema for fallback on analysis failure."""
    return {
        "is_complete": False,
        "missing_critical_fields": [],
        "clinical_highlights": [],
        "medication_notes": [],
        "follow_up_actions": [],
        "patient_friendly_summary": "",
    }


def analyze_discharge_summary(
    structured_data: Dict[str, Any],
    client: Optional[OpenAI] = None,
) -> Dict[str, Any]:
    """
    Analyze extracted discharge summary for completeness, medications, and follow-up.
    """
    if client is None:
        if not GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY not configured")
        client = OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL)

    logger.info("[DISCHARGE] Analysis started")

    prompt = """You are an expert clinical documentation reviewer helping a patient understand their discharge summary.

        You are given structured data extracted from a hospital discharge summary. Your job is to:
        1. Assess whether critical sections are present (diagnosis, medications at discharge, follow-up).
        2. Highlight clinically important findings in plain language (no new diagnoses—only reflect what is in the data).
        3. Summarize medication instructions clearly (doses, frequency) when present.
        4. List concrete follow-up actions (appointments, labs, warning signs to watch) when mentioned in the data.

        STRICT RULES:
        - Only use the provided structured data. Do NOT invent diagnoses, medications, or dates.
        - If something is missing, say so in missing_critical_fields rather than guessing.
        - Return STRICT JSON ONLY.

        ---

        RETURN JSON EXACTLY IN THIS STRUCTURE:

        {
        "is_complete": true/false,
        "missing_critical_fields": [
            "Names of important sections or fields that are blank or absent"
        ],
        "clinical_highlights": [
            "Short bullet-style points summarizing key diagnoses/procedures/course from the data"
        ],
        "medication_notes": [
            "Clear notes per medication or general med instructions when data exists"
        ],
        "follow_up_actions": [
            "Actionable next steps explicitly supported by the data (visits, tests, lifestyle)"
        ],
        "patient_friendly_summary": "A concise Markdown message in ENGLISH: brief overview, what to do next, and when to seek urgent care if the document mentions red flags. If red flags are not in the data, give general advice to follow the doctor's instructions."
        }

        ---

        DATA:
        <<<STRUCTURED_DATA>>>
        """

    messages = [
        {
            "role": "system",
            "content": (
                "You analyze discharge summary structured data and return analysis as strict JSON only."
            ),
        },
        {
            "role": "user",
            "content": prompt.replace("<<<STRUCTURED_DATA>>>", json.dumps(structured_data)),
        },
    ]

    try:
        response = client.chat.completions.create(
            model=CLAIM_VALIDATOR_MODEL,
            reasoning_effort="low",
            messages=messages,
            temperature=0.1,
            max_tokens=3000,
            response_format={"type": "json_object"},
        )
        response_content = response.choices[0].message.content.strip()
        parsed = json.loads(response_content)
        logger.info("[DISCHARGE] Analysis completed")
        return parsed
    except json.JSONDecodeError as e:
        logger.warning(f"[DISCHARGE] Failed to parse analysis JSON: {e}")
        return get_empty_discharge_analysis_schema()
    except Exception as e:
        logger.error(f"[DISCHARGE] Analysis failed: {type(e).__name__}: {e}")
        return get_empty_discharge_analysis_schema()


def _extract_ocr(file_bytes: bytes, filename: str) -> Optional[str]:
    """
    Run OCR on raw file bytes. Routes to image or PDF OCR based on file type.

    Returns extracted text or None on failure.
    """
    _, ext = os.path.splitext(filename)
    ext = ext.lstrip(".").lower() or "bin"

    if ext not in DISCHARGE_ALLOWED_EXTENSIONS:
        logger.warning(f"[DISCHARGE] Unsupported type .{ext} for {filename}")
        return None

    try:
        if _is_pdf(filename):
            s3_key = _upload_temp_to_s3(file_bytes, filename)
            try:
                ocr_text = extract_text_from_pdf_s3(S3_BUCKET, s3_key)
            finally:
                _cleanup_temp_s3(s3_key)
        else:
            ocr_text = extract_text_from_image(file_bytes)

        if not ocr_text or not ocr_text.strip():
            logger.warning(f"[DISCHARGE] No text extracted from {filename}")
            return None

        logger.info(f"[DISCHARGE] OCR extracted {len(ocr_text)} chars from {filename}")
        return ocr_text

    except Exception as e:
        logger.error(
            f"[DISCHARGE] OCR failed for {filename}: {type(e).__name__}: {e}"
        )
        return None


def extract_discharge_data_from_file(
    file_bytes: bytes,
    filename: str,
) -> Dict[str, Any]:
    """
    Full discharge summary pipeline for a file.

    Flow: OCR → structured extraction → analysis.

    Returns:
        Dict with 'structured_data' and 'analysis' keys.
    """
    start_time = time.time()
    logger.info(
        f"[DISCHARGE] Processing discharge file: {filename} ({len(file_bytes)} bytes)"
    )

    ocr_text = _extract_ocr(file_bytes, filename)
    if not ocr_text:
        elapsed = time.time() - start_time
        logger.info(f"[DISCHARGE] Pipeline completed in {elapsed:.2f}s — failed (OCR)")
        return {"structured_data": None, "analysis": None}

    try:
        structured_data = extract_discharge_structured_data_from_text(ocr_text)
        structured_data["source_filename"] = filename
        logger.info(
            f"[DISCHARGE] Structured extraction completed for {filename}, "
            f"category={structured_data.get('document_category', 'UNKNOWN')}"
        )
    except Exception as e:
        logger.error(
            f"[DISCHARGE] Structured extraction failed for {filename}: "
            f"{type(e).__name__}: {e}"
        )
        elapsed = time.time() - start_time
        logger.info(
            f"[DISCHARGE] Pipeline completed in {elapsed:.2f}s — failed (extraction)"
        )
        return {"structured_data": None, "analysis": None}

    analysis = analyze_discharge_summary(structured_data)

    elapsed = time.time() - start_time
    logger.info(f"[DISCHARGE] Pipeline completed in {elapsed:.2f}s — success")

    return {"structured_data": structured_data, "analysis": analysis}
