"""
Prescription service - prescription generation from patient context.

Handles ONLY the final GPT synthesis step. OCR and document extraction
are handled by extraction_service and context_service.
"""
import json
import time
from typing import Dict, Any

from fastapi import HTTPException
from openai import OpenAI

# from app.config import OPENAI_API_KEY
from app.config import GROQ_API_KEY, GROQ_BASE_URL, CLAIM_REASONING_MODEL
from app.logger import get_logger
from app.services.context_service import extract_patient_details_from_context

logger = get_logger(__name__)

_PATIENT_DETAIL_KEYS = ("name", "age", "gender", "diagnosis")


# def _require_openai_key() -> None:
#     """Raise HTTPException if OpenAI API key is not configured."""
#     if not OPENAI_API_KEY:
#         raise HTTPException(status_code=500, detail="OpenAI API key not configured")
def _require_grok_key() -> None:
    """Raise HTTPException if Groq API key is not configured."""
    if not GROQ_API_KEY:
        raise HTTPException(status_code=500, detail="Groq API key not configured")


def _build_prescription_result(
    prescription_text: str,
    patient_context: Dict[str, Any],
    log_prefix: str = "",
) -> Dict[str, Any]:
    """Build prescription response dict with optional patient_details."""
    patient_details = extract_patient_details_from_context(patient_context)
    has_details = any(
        patient_details.get(k) is not None for k in _PATIENT_DETAIL_KEYS
    )
    if has_details:
        if log_prefix:
            logger.info(f"{log_prefix} Patient details: {patient_details}")
        return {"prescription": prescription_text, "patient_details": patient_details}
    return {"prescription": prescription_text}


def _generate_prescription_from_context(
    patient_context: Dict[str, Any],
    client: OpenAI,
) -> str:
    """
    Generate final prescription text from chronological patient context.

    Args:
        patient_context: Patient context built by build_patient_context()
        client: OpenAI client instance

    Returns:
        Final prescription text
    """
    prompt_input = {
        "patient_summary": patient_context["patient_summary"],
        "document_history": patient_context["document_history"],
    }

    synthesis_prompt = """Generate a clear medical prescription using ONLY the patient context below.

        Structure the prescription as follows:

        1. **Clinical Summary** (first, mandatory section)
           - Maximum 4–5 lines.
           - Highlight only abnormal or borderline findings.
           - If lipid abnormalities are present, briefly mention potential cardiovascular relevance.
           - If CBC values are largely normal, state that they are within normal limits.
           - Do NOT exaggerate risk.
           - Do NOT invent diagnoses.

        2. **Structured sections** (after Clinical Summary, omit any that have no data):
           - Diagnosis (only if clearly supported by the data)
           - Key Findings
           - Medications (if present)
           - Advice / Lifestyle Recommendations
           - Follow-up Recommendations

        Rules:
        - Prefer newer documents when conflicts exist.
        - Detect trends across reports.
        - Do NOT hallucinate diagnoses.
        - Use ONLY provided information.
        - No duplicate information across sections.
        - Omit empty sections entirely.

        Return JSON in this format:
        {{
        "prescription": "full formatted text using \\n for line breaks"
        }}

        Patient context:
        <<<CONTEXT_JSON>>>
        """.replace("<<<CONTEXT_JSON>>>", json.dumps(prompt_input))

    messages = [
        {
            "role": "system",
            "content": (
                "You are given a chronological medical history of a patient "
                "extracted from multiple medical documents. Generate a clear, "
                "structured prescription using ONLY the provided information. "
                "Prefer newer documents when conflicts exist. Detect trends "
                "across reports. Do NOT hallucinate diagnoses. Use ONLY "
                "provided information."
            ),
        },
        {
            "role": "user",
            "content": synthesis_prompt,
        },
    ]

    try:
        response = client.chat.completions.create(
            # model="gpt-4o",
            model=CLAIM_REASONING_MODEL,
            messages=messages,
            temperature=0.3,
            max_tokens=3000,
            response_format={"type": "json_object"},
        )

        response_content = response.choices[0].message.content.strip()
        result_data = json.loads(response_content)
        return result_data.get("prescription", "")
    except Exception as e:
        logger.error(f"[SYNTHESIS] Failed to generate prescription: {type(e).__name__}: {str(e)}")
        raise


def generate_prescription_from_context(
    patient_context: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Generate a prescription from a pre-built patient context (v3 flow).

    Skips both OCR *and* per-document GPT extraction — those already ran
    at upload time.  This function runs a single GPT synthesis call over
    the chronological patient context.

    Args:
        patient_context: Patient context built by build_patient_context().

    Returns:
        Dict with 'prescription' key and optionally 'patient_details'.
    """
    start_time = time.time()

    doc_count = len(patient_context.get("document_history", []))
    logger.info(
        f"[PRESCRIPTION_V3] Synthesising from patient context "
        f"with {doc_count} document(s)"
    )

    # _require_openai_key()
    _require_grok_key()
    if not patient_context.get("document_history"):
        return {"prescription": ""}

    try:
        # client = OpenAI(api_key=OPENAI_API_KEY)
        client = OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL)
        prescription_text = _generate_prescription_from_context(patient_context, client)

        elapsed = time.time() - start_time
        logger.info(f"[PRESCRIPTION_V3] Pipeline completed in {elapsed:.2f}s")

        return _build_prescription_result(
            prescription_text, patient_context, log_prefix="[PRESCRIPTION_V3]"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {type(e).__name__}: {str(e)}")
        logger.exception("Full exception traceback:")
        raise HTTPException(
            status_code=500, detail=f"Failed to generate prescription: {str(e)}"
        )
