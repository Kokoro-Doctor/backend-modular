"""
Insurance extraction service - OCR and data extraction pipeline for insurance documents.

Supports both images (Textract detect_document_text) and PDFs (Textract analyze_document
via S3 reference).

Fully independent from the medical extraction pipeline in extraction_service.py.
"""
import json
import os
import time
from typing import Dict, Any, Optional
from uuid import uuid4

from openai import OpenAI

from app.config import (
    s3_client,
    S3_BUCKET,
    S3_FOLDER_PREFIX,
    ALLOWED_EXTENSIONS,
    GROQ_API_KEY,
    GROQ_BASE_URL,
    CLAIM_VALIDATOR_MODEL,
)
from app.logger import get_logger
from app.services.ocr_service import extract_text_from_image, extract_text_from_pdf_s3

logger = get_logger(__name__)

PDF_EXTENSIONS = {"pdf"}
IMAGE_EXTENSIONS = ALLOWED_EXTENSIONS
INSURANCE_ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS

MAX_CHARS_PER_DOC = 2000  # keeps requests under gpt-oss-20b's TPM limit


def _is_pdf(filename: str) -> bool:
    _, ext = os.path.splitext(filename)
    return ext.lstrip(".").lower() in PDF_EXTENSIONS


def _is_image(filename: str) -> bool:
    _, ext = os.path.splitext(filename)
    return ext.lstrip(".").lower() in IMAGE_EXTENSIONS


def _upload_temp_to_s3(file_bytes: bytes, filename: str) -> str:
    """Upload file to a temporary S3 location for Textract processing."""
    temp_id = uuid4().hex[:8]
    _, ext = os.path.splitext(filename)
    ext = ext.lstrip(".").lower() or "pdf"
    s3_key = f"{S3_FOLDER_PREFIX}_temp/insurance/{temp_id}/document.{ext}"

    s3_client.put_object(
        Bucket=S3_BUCKET,
        Key=s3_key,
        Body=file_bytes,
    )
    logger.info(f"[INSURANCE] Uploaded temp file to s3://{S3_BUCKET}/{s3_key}")
    return s3_key


def _cleanup_temp_s3(s3_key: str) -> None:
    """Best-effort cleanup of temporary S3 object after processing."""
    try:
        s3_client.delete_object(Bucket=S3_BUCKET, Key=s3_key)
        logger.info(f"[INSURANCE] Cleaned up temp file s3://{S3_BUCKET}/{s3_key}")
    except Exception as e:
        logger.warning(f"[INSURANCE] Failed to cleanup temp file {s3_key}: {e}")


def get_empty_insurance_schema() -> Dict[str, Any]:
    """Return empty insurance schema for fallback on parse failure."""
    return {
        "document_category": "INSURANCE_FORM",
        "patient_details": {
            "name": None,
            "age": None,
            "gender": None,
        },
        "insurance_details": {
            "insurance_company": None,
            "policy_name": None,
            "policy_number": None,
        },
        "hospital_details": {
            "hospital_name": None,
            "admission_date": None,
            "discharge_date": None,
        },
        "claim_details": {
            "treatment": None,
            "bill_amount": None,
            "claimed_amount": None,
            "documents_submitted": [],
        },
        "document_metadata": {
            "document_date": None,
        },
        "document_summary": "",
    }


def extract_insurance_structured_data_from_text(
    ocr_text: str,
    client: Optional[OpenAI] = None,
) -> Dict[str, Any]:
    """
    Extract structured insurance data from OCR text using GPT.

    Uses insurance-specific schema and prompt. Fully independent from
    medical extraction in extraction_service.py.
    """
    if client is None:
        if not GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY not configured")
        client = OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL)

    prompt = """You are an insurance claim data extraction system.

        Extract ONLY explicitly present information from the document.

        STRICT RULES:
        1. Do NOT infer or assume anything
        2. Missing values → null
        3. Lists → []
        4. Return STRICT JSON ONLY
        5. Do NOT add extra fields

        --------------------------------------------------

        RETURN JSON IN EXACT STRUCTURE:

        {
        "document_category": "INSURANCE_FORM",

        "patient_details": {
            "name": null,
            "age": null,
            "gender": null
        },

        "insurance_details": {
            "insurance_company": null,
            "policy_name": null,
            "policy_number": null
        },

        "hospital_details": {
            "hospital_name": null,
            "admission_date": null,
            "discharge_date": null
        },

        "claim_details": {
            "treatment": null,
            "bill_amount": null,
            "claimed_amount": null,
            "documents_submitted": []
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
            "content": "You extract structured insurance/claim data from OCR text. Return strict JSON only.",
        },
        {
            "role": "user",
            "content": prompt.replace("<<<OCR_TEXT>>>", ocr_text),
        },
    ]

    try:
        response = client.chat.completions.create(
            model=CLAIM_VALIDATOR_MODEL,
                        messages=messages,
            temperature=0.1,
            max_tokens=3000,
            response_format={"type": "json_object"},
        )
        response_content = response.choices[0].message.content.strip()
        parsed = json.loads(response_content)
        return parsed
    except json.JSONDecodeError as e:
        logger.warning(f"[INSURANCE] Failed to parse extraction JSON: {e}")
        return get_empty_insurance_schema()
    except Exception as e:
        logger.error(f"[INSURANCE] Extraction failed: {type(e).__name__}: {e}")
        return get_empty_insurance_schema()


def get_empty_analysis_schema() -> Dict[str, Any]:
    """Return empty analysis schema for fallback on analysis failure."""
    return {
        "is_complete": False,
        "missing_fields": [],
        "issues": [],
        "suggestions": [],
        "claim_opportunity": "",
    }


def analyze_insurance_claim(
    structured_data: Dict[str, Any],
    client: Optional[OpenAI] = None,
) -> Dict[str, Any]:
    """
    Analyze extracted insurance claim data for completeness, issues, and opportunities.

    Runs after structured extraction to provide claim validation.
    """
    if client is None:
        if not GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY not configured")
        client = OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL)

    logger.info("[INSURANCE] Analysis started")

    prompt = """You are an expert Health Insurance Claims Auditor. 
        Your goal is to perform a LINE-BY-LINE audit of the extracted insurance form data.

        ### STEP 1: LINE-BY-LINE AUDIT & RECTIFICATION
        Scan every field (Patient Name, Policy No, Bank Details, Hospital Name, Bill Amounts).
        For EVERY field where you find an issue (spelling, missing data, format error, or mismatch):
        - Identify the "Field Name".
        - State the "Issue" (e.g., 'STATA' instead of 'STATE').
        - Provide the "Correction" (How to fix it).

        ### STEP 2: CLAIM OPPORTUNITY & REVISED CALCULATION
        - Compare 'bill_amount' vs 'claimed_amount'. 
        - If 'claimed_amount' is less than 'bill_amount', the difference is a missed opportunity.
        - If bill amounts are missing but pharmacy/pre-post hospital docs are mentioned, estimate the missing value.
        
        **CRITICAL CALCULATION RULE (Internal Only):**
        - Calculate a 'Potential Revised Total'.
        - If you cannot find a logical way to calculate the increase, apply a hidden 5% to 10% uplift to the current 'claimed_amount' or 'bill_amount'. 
        - DO NOT mention to the user that you added a percentage. Present it as "potential claimable value after correcting errors and adding missing headers."

        ### STEP 3: POLICY INSIGHTS
        - Identify the company or fallback to 'Star Health & Allied Insurance'.
        - Mention rules: Original docs required, 30/60 days limits, and specific TPA mandates.

        ---
        RETURN JSON EXACTLY IN THIS STRUCTURE:

        {
        "is_complete": true/false,
        "missing_fields": [],
        "line_by_line_audit": [
            {
                "field": "Field Name",
                "current_value": "Value found",
                "issue": "Specific error",
                "correction": "Exact step to fix"
            }
        ],
        "financial_summary": {
            "current_claimed_amount": "Total from form",
            "potential_revised_amount": "New calculated total (with hidden 5-10% uplift if needed)",
            "additional_benefit": "The difference between the two"
        },
        "policy_insights": [],
        "bot_message": "Use Markdown. Structure it as: 
        ### 💰 1. Your Claim Opportunity
        (Tell them they can claim [potential_revised_amount] instead of [current_claimed_amount] if they fix the errors below).
        
        ### 🔍 2. Line-by-Line Audit
        (Table or Bullet points showing: Field -> Issue -> Fix).
        
        ### 🏢 3. Policy Insights ([Company Name])
        (Relevant rules).
        
        ### ✅ 4. Final Recommendation
        (Suggest a fresh printout and how to submit for the higher amount)."
        }

        ---
        DATA:
        <<<STRUCTURED_DATA>>>
        """

    messages = [
        {
            "role": "system",
            "content": "You validate insurance claim data and return structured analysis as strict JSON only.",
        },
        {
            "role": "user",
            "content": prompt.replace("<<<STRUCTURED_DATA>>>", json.dumps(structured_data)),
        },
    ]

    try:
        response = client.chat.completions.create(
            model=CLAIM_VALIDATOR_MODEL,
                        messages=messages,
            temperature=0.1,
            max_tokens=3000,
            response_format={"type": "json_object"},
        )
        response_content = response.choices[0].message.content.strip()
        parsed = json.loads(response_content)
        logger.info("[INSURANCE] Analysis completed")
        return parsed
    except json.JSONDecodeError as e:
        logger.warning(f"[INSURANCE] Failed to parse analysis JSON: {e}")
        return get_empty_analysis_schema()
    except Exception as e:
        logger.error(f"[INSURANCE] Analysis failed: {type(e).__name__}: {e}")
        return get_empty_analysis_schema()


def _extract_ocr(file_bytes: bytes, filename: str) -> Optional[str]:
    """
    Run OCR on raw file bytes. Routes to image or PDF OCR based on file type.

    Returns extracted text or None on failure.
    """
    _, ext = os.path.splitext(filename)
    ext = ext.lstrip(".").lower() or "bin"

    if ext not in INSURANCE_ALLOWED_EXTENSIONS:
        logger.warning(f"[INSURANCE] Unsupported type .{ext} for {filename}")
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
            logger.warning(f"[INSURANCE] No text extracted from {filename}")
            return None

        logger.info(f"[INSURANCE] OCR extracted {len(ocr_text)} chars from {filename}")
        return ocr_text

    except Exception as e:
        logger.error(
            f"[INSURANCE] OCR failed for {filename}: "
            f"{type(e).__name__}: {e}"
        )
        return None


def extract_insurance_data_from_file(
    file_bytes: bytes,
    filename: str,
) -> Dict[str, Any]:
    """
    Full insurance extraction pipeline for a file.

    Flow: OCR → structured extraction → claim analysis.

    Args:
        file_bytes: Raw file bytes (from multipart upload).
        filename: Original filename (used for file-type detection).

    Returns:
        Dict with 'structured_data' and 'analysis' keys.
        Either value may be None if that step failed.
    """
    start_time = time.time()
    logger.info(f"[INSURANCE] Processing insurance file: {filename} ({len(file_bytes)} bytes)")

    ocr_text = _extract_ocr(file_bytes, filename)
    if not ocr_text:
        elapsed = time.time() - start_time
        logger.info(f"[INSURANCE] Pipeline completed in {elapsed:.2f}s — failed (OCR)")
        return {"structured_data": None, "analysis": None}

    try:
        structured_data = extract_insurance_structured_data_from_text(ocr_text)
        structured_data["source_filename"] = filename
        logger.info(
            f"[INSURANCE] Structured extraction completed for {filename}, "
            f"category={structured_data.get('document_category', 'UNKNOWN')}"
        )
        logger.info(
            "[INSURANCE] Extracted structured_data: %s",
            json.dumps(structured_data, ensure_ascii=False, default=str),
        )
    except Exception as e:
        logger.error(
            f"[INSURANCE] Structured extraction failed for {filename}: "
            f"{type(e).__name__}: {e}"
        )
        elapsed = time.time() - start_time
        logger.info(f"[INSURANCE] Pipeline completed in {elapsed:.2f}s — failed (extraction)")
        return {"structured_data": None, "analysis": None}

    analysis = analyze_insurance_claim(structured_data)

    elapsed = time.time() - start_time
    logger.info(f"[INSURANCE] Pipeline completed in {elapsed:.2f}s — success")

    # TODO: If PDF size > threshold, split pages and process in parallel

    return {"structured_data": structured_data, "analysis": analysis}
