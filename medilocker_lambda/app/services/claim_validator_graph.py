"""
LangGraph-based insurance claim validation pipeline.

Nodes:
  1. ocr_extractor         — Textract OCR all docs (no LLM)
  2. claim_field_extractor  — Claim form OCR → structured JSON (LLM)
  3. policy_router          — Detect insurer/TPA (no LLM)
  4a. cross_doc_analyzer    — Compare claim vs other docs (LLM, conditional)
  4b. claim_auditor         — Line-by-line CoT audit + error ID (LLM)
      + financial_calculator — Deterministic Python math (no LLM)
  5. report_generator       — Final report (LLM)
"""
import json
import os
import time
import re
from typing import Dict, Any, Optional, List, TypedDict, Annotated
from uuid import uuid4

from openai import OpenAI
from langgraph.graph import StateGraph, START, END

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
from app.services.policy_router import detect_policy_baseline
from app.services.financial_calculator import calculate_deduction_risk
from app.services.icd_lookup import lookup_icd_code
from app.services.prompts.claim_prompts import (
    EXTRACTION_SYSTEM, EXTRACTION_USER,
    CROSS_DOC_SYSTEM, CROSS_DOC_USER,
    AUDITOR_SYSTEM, AUDITOR_USER,
    REPORT_SYSTEM, REPORT_USER,
    MULTI_DOC_EXTRACT_SYSTEM, MULTI_DOC_EXTRACT_USER,
    FORM_FILLER_SYSTEM, FORM_FILLER_USER,
)

logger = get_logger(__name__)

PDF_EXTENSIONS = {"pdf"}
IMAGE_EXTENSIONS = ALLOWED_EXTENSIONS
INSURANCE_ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS


# ── State Schema ─────────────────────────────────────────────────────

def _merge_dicts(original: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    merged = original.copy() if original else {}
    merged.update(update or {})
    return merged


class ClaimValidationState(TypedDict):
    documents: Dict[str, bytes]
    filenames: Dict[str, str]
    ocr_texts: Dict[str, str]
    structured_data: Optional[Dict[str, Any]]
    policy_baseline: str
    policy_type: str
    cross_doc_findings: Optional[Dict[str, Any]]
    thinking_trace: List[str]
    audit_results: Optional[Dict[str, Any]]
    final_report: Optional[Dict[str, Any]]
    error: Optional[str]
    timings: Annotated[Dict[str, float], _merge_dicts]
    autofill_extracted: Optional[Dict[str, Any]]
    autofill_result: Optional[Dict[str, Any]]

# ── Helpers ──────────────────────────────────────────────────────────

def _get_client() -> OpenAI:
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY not configured")
    return OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL)


def _upload_temp_to_s3(file_bytes, filename):
    temp_id = uuid4().hex[:8]
    _, ext = os.path.splitext(filename)
    ext = ext.lstrip(".").lower() or "pdf"
    s3_key = f"{S3_FOLDER_PREFIX}_temp/insurance/{temp_id}/document.{ext}"
    s3_client.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=file_bytes)
    return s3_key


def _cleanup_temp_s3(s3_key):
    try:
        s3_client.delete_object(Bucket=S3_BUCKET, Key=s3_key)
    except Exception:
        pass


def _is_pdf(filename):
    _, ext = os.path.splitext(filename)
    return ext.lstrip(".").lower() in PDF_EXTENSIONS


def _extract_thinking_blocks(text):
    return re.findall(r"<thinking>(.*?)</thinking>", text, re.DOTALL)


# ═════════════════════════════════════════════════════════════════════
# NODE 1: Multi-Document OCR
# ═════════════════════════════════════════════════════════════════════

def ocr_extractor(state: ClaimValidationState) -> dict:
    start = time.time()
    documents = state.get("documents", {})
    filenames = state.get("filenames", {})

    if not documents:
        return {
            "ocr_texts": {},
            "error": "No documents provided",
            "timings": {"ocr": time.time() - start},
        }

    import concurrent.futures

    def ocr_single_doc(doc_type, file_bytes, filename):
        _, ext = os.path.splitext(filename)
        ext = ext.lstrip(".").lower() or "bin"

        if ext not in INSURANCE_ALLOWED_EXTENSIONS:
            return doc_type, None

        try:
            if _is_pdf(filename):
                s3_key = _upload_temp_to_s3(file_bytes, filename)
                try:
                    text = extract_text_from_pdf_s3(S3_BUCKET, s3_key)
                finally:
                    _cleanup_temp_s3(s3_key)
            else:
                text = extract_text_from_image(file_bytes)

            if text and text.strip():
                logger.info(f"[CLAIM_GRAPH] OCR {doc_type}: {len(text)} chars")
                return doc_type, text
            return doc_type, None
        except Exception as e:
            logger.error(f"[CLAIM_GRAPH] OCR failed for {doc_type}: {e}")
            return doc_type, None

    ocr_texts = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(documents)) as executor:
        futures = {
            executor.submit(
                ocr_single_doc, doc_type, file_bytes, filenames.get(doc_type, f"{doc_type}.pdf")
            ): doc_type
            for doc_type, file_bytes in documents.items()
        }
        for future in concurrent.futures.as_completed(futures):
            doc_type, text = future.result()
            if text:
                ocr_texts[doc_type] = text

    if not ocr_texts:
        return {
            "ocr_texts": {},
            "error": "Failed to extract text from any document",
            "timings": {"ocr": time.time() - start},
        }

    logger.info(
        f"[CLAIM_GRAPH] Node 1: {len(ocr_texts)} doc(s) OCR'd in {time.time()-start:.2f}s"
    )
    return {"ocr_texts": ocr_texts, "timings": {"ocr": time.time() - start}}


# ═════════════════════════════════════════════════════════════════════
# FLOW ROUTER — decides AUDIT vs AUTOFILL based on uploaded docs
# ═════════════════════════════════════════════════════════════════════

def flow_router(state: ClaimValidationState) -> str:
    """Route to audit path or autofill path based on what was uploaded."""
    ocr_texts = state.get("ocr_texts", {})

    if "claim_form" in ocr_texts:
        logger.info("[CLAIM_GRAPH] Flow router → AUDIT path (claim form found)")
        return "claim_field_extractor"
    else:
        logger.info("[CLAIM_GRAPH] Flow router → AUTOFILL path (no claim form)")
        return "multi_doc_extractor"


# ═════════════════════════════════════════════════════════════════════
# AUTOFILL NODE 1: Multi-Document Extractor
# ═════════════════════════════════════════════════════════════════════

def multi_doc_extractor(state: ClaimValidationState) -> dict:
    """Extract all patient/hospital/billing data from whatever docs are available."""
    start = time.time()
    client = _get_client()

    ocr_texts = state.get("ocr_texts", {})

    # Build combined doc text with labels
    all_docs_text = ""
    for doc_type, text in ocr_texts.items():
        label = doc_type.replace("_", " ").title()
        all_docs_text += f"\n\n--- {label} ---\n{text}\n"

    prompt = MULTI_DOC_EXTRACT_USER.replace("<<<ALL_DOCS>>>", all_docs_text)

    try:
        response = client.chat.completions.create(
            model=CLAIM_VALIDATOR_MODEL,
            messages=[
                {"role": "system", "content": MULTI_DOC_EXTRACT_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content.strip()
        extracted = json.loads(raw)

        # ── Deterministic ICD-10 verification on extracted data ──
        try:
            diag_info = extracted.get("diagnosis_and_procedures", {}) or {}
            primary_diag = diag_info.get("primary_diagnosis", "")
            llm_icd = diag_info.get("primary_icd_code")

            if primary_diag:
                icd_result = lookup_icd_code(primary_diag, llm_suggested_code=llm_icd)
                if icd_result["code"]:
                    diag_info["primary_icd_code"] = icd_result["code"]
                    diag_info["icd_verified"] = True
                    diag_info["icd_confidence"] = icd_result["confidence"]
                    diag_info["icd_method"] = icd_result["method"]

                    logger.info(
                        f"[CLAIM_GRAPH] Multi-doc ICD: '{primary_diag}' → "
                        f"{icd_result['code']} ({icd_result['confidence']:.0%})"
                    )
        except Exception as e:
            logger.warning(f"[CLAIM_GRAPH] Multi-doc ICD lookup failed (non-fatal): {e}")


        logger.info(
            f"[CLAIM_GRAPH] Multi-doc extraction done in {time.time()-start:.2f}s"
        )
        return {
            "autofill_extracted": extracted,
            "timings": {"multi_doc_extraction": time.time() - start},
        }

    except Exception as e:
        logger.error(f"[CLAIM_GRAPH] Multi-doc extraction failed: {e}")
        return {
            "autofill_extracted": None,
            "error": f"Multi-doc extraction failed: {e}",
            "timings": {"multi_doc_extraction": time.time() - start},
        }


# ═════════════════════════════════════════════════════════════════════
# AUTOFILL NODE 2: Claim Form Filler
# ═════════════════════════════════════════════════════════════════════

def claim_form_filler(state: ClaimValidationState) -> dict:
    """Map extracted data to Medi Assist form sections."""
    start = time.time()
    client = _get_client()

    extracted = state.get("autofill_extracted")
    if not extracted:
        return {
            "autofill_result": None,
            "error": "No extracted data available for form filling",
            "timings": {"form_filling": time.time() - start},
        }

    prompt = FORM_FILLER_USER.replace(
        "<<<EXTRACTED_DATA>>>", json.dumps(extracted, indent=2)
    )

    try:
        response = client.chat.completions.create(
            model=CLAIM_VALIDATOR_MODEL,
            messages=[
                {"role": "system", "content": FORM_FILLER_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content.strip()
        form_data = json.loads(raw)

        # ── Deterministic ICD-10 code verification for autofill ──
        try:
            extracted = state.get("autofill_extracted", {}) or {}
            diag_info = extracted.get("diagnosis_and_procedures", {}) or {}
            primary_diag = diag_info.get("primary_diagnosis", "")
            llm_icd = diag_info.get("primary_icd_code")

            if primary_diag:
                icd_result = lookup_icd_code(primary_diag, llm_suggested_code=llm_icd)
                logger.info(
                    f"[CLAIM_GRAPH] Autofill ICD lookup: '{primary_diag}' → "
                    f"{icd_result['code']} ({icd_result['confidence']:.0%}, {icd_result['method']})"
                )

                # Update the extracted data
                if icd_result["code"]:
                    diag_info["primary_icd_code"] = icd_result["code"]
                    diag_info["icd_verified"] = True
                    diag_info["icd_confidence"] = icd_result["confidence"]

                # Update form_data part_b with verified code
                part_b = form_data.get("part_b_hospital_section", {})
                if part_b and icd_result["code"]:
                    part_b["primary_icd_code"] = icd_result["code"]

                # Also verify additional diagnosis if present
                additional_diag = diag_info.get("additional_diagnosis")
                additional_llm_icd = diag_info.get("additional_icd_code")
                if additional_diag:
                    add_result = lookup_icd_code(additional_diag, llm_suggested_code=additional_llm_icd)
                    if add_result["code"]:
                        diag_info["additional_icd_code"] = add_result["code"]
                        if part_b:
                            part_b["additional_icd_code"] = add_result["code"]

        except Exception as e:
            logger.warning(f"[CLAIM_GRAPH] Autofill ICD lookup failed (non-fatal): {e}")

        # Calculate claimable amount from billing data
        billing = extracted.get("billing_details", {}) or {}
        total_bill = 0
        for key, val in billing.items():
            if val and key != "package_charges":
                parsed = _parse_billing_amount(val)
                if parsed > 0 and key == "total_bill_amount":
                    total_bill = parsed

        if total_bill == 0:
            # Sum individual items
            for key, val in billing.items():
                if val and key not in ("total_bill_amount", "package_charges"):
                    total_bill += _parse_billing_amount(val)

        if form_data.get("claimable_summary"):
            form_data["claimable_summary"]["total_bill"] = total_bill
            if not form_data["claimable_summary"].get("estimated_claimable"):
                form_data["claimable_summary"]["estimated_claimable"] = total_bill

        logger.info(
            f"[CLAIM_GRAPH] Form filling done, total_bill=₹{total_bill} "
            f"in {time.time()-start:.2f}s"
        )
        return {
            "autofill_result": form_data,
            "timings": {"form_filling": time.time() - start},
        }

    except Exception as e:
        logger.error(f"[CLAIM_GRAPH] Form filling failed: {e}")
        return {
            "autofill_result": None,
            "error": f"Form filling failed: {e}",
            "timings": {"form_filling": time.time() - start},
        }


def _parse_billing_amount(val):
    """Parse a billing amount from various formats."""
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        cleaned = val.replace("₹", "").replace(",", "").replace(" ", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0
    return 0.0

# ═════════════════════════════════════════════════════════════════════
# NODE 2: Claim Field Extractor
# ═════════════════════════════════════════════════════════════════════

def claim_field_extractor(state: ClaimValidationState) -> dict:
    start = time.time()
    client = _get_client()

    claim_text = state.get("ocr_texts", {}).get("claim_form", "")
    if not claim_text:
        return {
            "structured_data": None,
            "error": "No claim form text",
            "timings": {"extraction": time.time() - start},
        }

    prompt = EXTRACTION_USER.replace("<<<OCR_TEXT>>>", claim_text)

    try:
        response = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content.strip()
        structured = json.loads(raw)
        structured["source_filename"] = state.get("filenames", {}).get("claim_form", "unknown")

        logger.info(f"[CLAIM_GRAPH] Node 2: extraction done in {time.time()-start:.2f}s")
        return {"structured_data": structured, "timings": {"extraction": time.time() - start}}

    except Exception as e:
        logger.error(f"[CLAIM_GRAPH] Extraction failed: {e}")
        return {
            "structured_data": None,
            "error": f"Extraction failed: {e}",
            "timings": {"extraction": time.time() - start},
        }


# ═════════════════════════════════════════════════════════════════════
# NODE 3: Policy Router
# ═════════════════════════════════════════════════════════════════════

def policy_router(state: ClaimValidationState) -> dict:
    start = time.time()
    baseline, p_type = detect_policy_baseline(state["structured_data"])
    logger.info(f"[CLAIM_GRAPH] Node 3: {baseline} ({p_type})")
    return {
        "policy_baseline": baseline,
        "policy_type": p_type,
        "timings": {"routing": time.time() - start},
    }


# ═════════════════════════════════════════════════════════════════════
# NODE 4a: Cross-Document Analyzer (runs only if extra docs exist)
# ═════════════════════════════════════════════════════════════════════

def cross_doc_analyzer(state: ClaimValidationState) -> dict:
    start = time.time()
    ocr_texts = state.get("ocr_texts", {})

    # Get supporting docs (everything except claim_form)
    supporting = {k: v for k, v in ocr_texts.items() if k != "claim_form"}

    if not supporting:
        logger.info("[CLAIM_GRAPH] Node 4a: No supporting docs, skipping")
        return {
            "cross_doc_findings": None,
            "timings": {"cross_doc": time.time() - start},
        }

    client = _get_client()

    # Build supporting docs text
    docs_text = ""
    for doc_type, text in supporting.items():
        label = doc_type.replace("_", " ").title()
        docs_text += f"\n--- {label} ---\n{text}\n"

    prompt = (
        CROSS_DOC_USER
        .replace("<<<STRUCTURED_DATA>>>", json.dumps(state["structured_data"], indent=2))
        .replace("<<<SUPPORTING_DOCS>>>", docs_text)
    )

    try:
        response = client.chat.completions.create(
            model=CLAIM_VALIDATOR_MODEL,
            messages=[
                {"role": "system", "content": CROSS_DOC_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content.strip()
        findings = json.loads(raw)

        num_findings = len(findings.get("cross_doc_findings", []))
        logger.info(
            f"[CLAIM_GRAPH] Node 4a: {num_findings} cross-doc findings "
            f"in {time.time()-start:.2f}s"
        )
        return {
            "cross_doc_findings": findings,
            "timings": {"cross_doc": time.time() - start},
        }

    except Exception as e:
        logger.error(f"[CLAIM_GRAPH] Cross-doc analysis failed: {e}")
        return {
            "cross_doc_findings": None,
            "timings": {"cross_doc": time.time() - start},
        }


# ═════════════════════════════════════════════════════════════════════
# NODE 4b: Claim Auditor + Financial Calculator
# ═════════════════════════════════════════════════════════════════════

def claim_auditor(state: ClaimValidationState) -> dict:
    start = time.time()
    client = _get_client()

    # Build cross-doc section for the prompt
    cross_doc_section = ""
    cross_doc_errors = []
    if state.get("cross_doc_findings"):
        findings = state["cross_doc_findings"]
        if findings.get("cross_doc_findings"):
            cross_doc_section = (
                "## CROSS-DOCUMENT FINDINGS\n\n"
                "The following mismatches were found between the claim form and supporting documents. "
                "Factor these into your audit:\n\n"
            )
            for f in findings["cross_doc_findings"]:
                cross_doc_section += (
                    f"- **{f.get('finding_type', 'unknown')}**: {f.get('description', '')}\n"
                    f"  Claim form: {f.get('claim_form_value', 'N/A')} | "
                    f"Supporting doc: {f.get('supporting_doc_value', 'N/A')} "
                    f"(from {f.get('source_doc', 'unknown')})\n\n"
                )
            cross_doc_errors = findings.get("cross_doc_errors", [])

    prompt = (
        AUDITOR_USER
        .replace("<<<POLICY_BASELINE>>>", state["policy_baseline"])
        .replace("<<<POLICY_TYPE>>>", state["policy_type"])
        .replace("<<<CROSS_DOC_SECTION>>>", cross_doc_section)
        .replace("<<<STRUCTURED_DATA>>>", json.dumps(state["structured_data"], indent=2))
    )

    try:
        response = client.chat.completions.create(
            model=CLAIM_VALIDATOR_MODEL,
            messages=[
                {"role": "system", "content": AUDITOR_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=8192,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content.strip()
        audit = json.loads(raw)

        # Extract thinking trace
        thinking_trace = audit.pop("thinking_trace", [])
        if not thinking_trace:
            thinking_trace = _extract_thinking_blocks(raw)

        # Combine LLM-identified errors + cross-doc errors
        identified_errors = audit.pop("identified_errors", [])
        all_errors = list(set(identified_errors + cross_doc_errors))

        # ── Deterministic ICD-10 code verification ──
        try:
            structured = state.get("structured_data", {}) or {}
            diag_info = structured.get("diagnosis_and_procedures", {}) or {}

            primary_diag = diag_info.get("primary_diagnosis", "")
            llm_icd_codes = diag_info.get("icd_codes", [])
            llm_primary_icd = llm_icd_codes[0] if llm_icd_codes else None

            if primary_diag:
                icd_result = lookup_icd_code(primary_diag, llm_suggested_code=llm_primary_icd)
                logger.info(
                    f"[CLAIM_GRAPH] ICD lookup: '{primary_diag}' → "
                    f"{icd_result['code']} ({icd_result['confidence']:.0%}, {icd_result['method']})"
                )

                # Update structured data with verified ICD code
                if icd_result["code"]:
                    if not diag_info.get("icd_codes"):
                        diag_info["icd_codes"] = []
                    # Replace first code or add verified one
                    if diag_info["icd_codes"]:
                        diag_info["icd_codes"][0] = icd_result["code"]
                    else:
                        diag_info["icd_codes"].append(icd_result["code"])

                # Add to medical_code_audit in audit results
                if not audit.get("medical_code_audit"):
                    audit["medical_code_audit"] = []

                audit["medical_code_audit"].append({
                    "code": icd_result["code"],
                    "type": "ICD-10",
                    "status": "VERIFIED" if icd_result["confidence"] >= 0.8 else "LOW_CONFIDENCE",
                    "reason": f"Deterministic DB lookup: {icd_result['method']} "
                              f"(confidence: {icd_result['confidence']:.0%})",
                })

                # If LLM code was wrong, flag it
                if icd_result.get("llm_code_overridden"):
                    audit["medical_code_audit"].append({
                        "code": icd_result["llm_code_overridden"],
                        "type": "ICD-10",
                        "status": "OVERRIDDEN",
                        "reason": f"LLM suggested {icd_result['llm_code_overridden']}, "
                                  f"corrected to {icd_result['code']} by deterministic lookup",
                    })

        except Exception as e:
            logger.warning(f"[CLAIM_GRAPH] ICD lookup failed (non-fatal): {e}")

        # ── Deterministic financial calculation ──
        financial_result = calculate_deduction_risk(
            structured_data=state["structured_data"],
            identified_errors=all_errors,
        )
        audit["financial_analysis"] = financial_result

        logger.info(
            f"[CLAIM_GRAPH] Node 4b: "
            f"{len(audit.get('red_flags', []))} red, "
            f"{len(audit.get('moderate_flags', []))} moderate, "
            f"at_risk=₹{financial_result['at_risk_amount']} "
            f"in {time.time()-start:.2f}s"
        )
        # Build report in Python — no LLM call needed
        final_report = build_report_from_audit(
            audit, state["policy_baseline"], state["policy_type"]
        )

        return {
            "thinking_trace": thinking_trace,
            "audit_results": audit,
            "final_report": final_report,
            "timings": {"audit": time.time() - start},
        }

    except Exception as e:
        logger.error(f"[CLAIM_GRAPH] Audit failed: {e}")
        return {
            "thinking_trace": [],
            "audit_results": None,
            "error": f"Audit failed: {e}",
            "timings": {"audit": time.time() - start},
        }


# ═════════════════════════════════════════════════════════════════════
# NODE 5: Report Generator
# ═════════════════════════════════════════════════════════════════════

def build_report_from_audit(audit_results, policy_baseline, policy_type):
    """Build final report from audit results — pure Python, no LLM."""
    financial = audit_results.get("financial_analysis", {})
    red_flags = audit_results.get("red_flags", [])
    moderate_flags = audit_results.get("moderate_flags", [])
    clean_fields = audit_results.get("clean_fields", [])
    medical_codes = audit_results.get("medical_code_audit", [])

    # Build suggestions from corrections
    suggestions = []
    for f in red_flags:
        if f.get("correction"):
            suggestions.append(f["correction"])
    for f in moderate_flags:
        if f.get("correction"):
            suggestions.append(f["correction"])

    # Medical code summary
    valid_codes = [c for c in medical_codes if c.get("status") == "VALID"]
    invalid_codes = [c for c in medical_codes if c.get("status") != "VALID"]

    # Build bot_message markdown
    bot_lines = []
    bot_lines.append(f"### Executive Summary")
    bot_lines.append(audit_results.get("executive_summary", ""))
    bot_lines.append("")

    bot_lines.append(f"### Financial Impact")
    bot_lines.append(financial.get("summary", ""))
    bot_lines.append("")

    if red_flags:
        bot_lines.append(f"### Red Flags")
        for f in red_flags:
            bot_lines.append(f"- **{f.get('field', '')}**: {f.get('issue', '')}. {f.get('correction', '')}.")
        bot_lines.append("")

    if moderate_flags:
        bot_lines.append(f"### Moderate Issues")
        for f in moderate_flags:
            bot_lines.append(f"- **{f.get('field', '')}**: {f.get('issue', '')}. {f.get('correction', '')}.")
        bot_lines.append("")

    if suggestions:
        bot_lines.append(f"### Suggestions")
        for i, s in enumerate(suggestions, 1):
            bot_lines.append(f"{i}. {s}")

    return {
        "executive_summary": audit_results.get("executive_summary", ""),
        "inconsistencies": {
            "red_flags": red_flags,
            "moderate_flags": moderate_flags,
            "do_not_touch": clean_fields,
        },
        "financial_summary": {
            "bill_amount": financial.get("bill_amount", 0),
            "expected_approval_as_is": financial.get("expected_approval_as_is", 0),
            "at_risk_amount": financial.get("at_risk_amount", 0),
            "recoverable_amount": financial.get("recoverable_amount", 0),
            "breakdown": financial.get("summary", ""),
        },
        "medical_code_summary": {
            "total_codes_found": len(medical_codes),
            "valid_codes": len(valid_codes),
            "invalid_codes": len(invalid_codes),
            "details": ", ".join([f"{c.get('code')} ({c.get('status')})" for c in medical_codes]) if medical_codes else "No codes found",
        },
        "final_suggestions": suggestions,
        "bot_message": "\n".join(bot_lines),
    }

def report_generator(state: ClaimValidationState) -> dict:
    start = time.time()
    client = _get_client()

    # Send only what the report needs — skip heavy fields
    slim_audit = {
        "executive_summary": state["audit_results"].get("executive_summary"),
        "red_flags": state["audit_results"].get("red_flags"),
        "moderate_flags": state["audit_results"].get("moderate_flags"),
        "clean_fields": state["audit_results"].get("clean_fields"),
        "medical_code_audit": state["audit_results"].get("medical_code_audit"),
        "financial_analysis": state["audit_results"].get("financial_analysis"),
    }

    prompt = (
        REPORT_USER
        .replace("<<<POLICY_BASELINE>>>", state["policy_baseline"])
        .replace("<<<POLICY_TYPE>>>", state["policy_type"])
        .replace("<<<AUDIT_RESULTS>>>", json.dumps(slim_audit))
    )

    try:
        response = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[
                {"role": "system", "content": REPORT_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content.strip()
        report = json.loads(raw)

        logger.info(f"[CLAIM_GRAPH] Node 5: report done in {time.time()-start:.2f}s")
        return {"final_report": report, "timings": {"report": time.time() - start}}

    except Exception as e:
        logger.error(f"[CLAIM_GRAPH] Report failed: {e}")
        return {
            "final_report": None,
            "error": f"Report failed: {e}",
            "timings": {"report": time.time() - start},
        }


# ═════════════════════════════════════════════════════════════════════
# Conditional Edges
# ═════════════════════════════════════════════════════════════════════

def after_ocr(state):
    ocr_texts = state.get("ocr_texts", {})
    if not ocr_texts:
        return END
    # Route based on whether claim_form exists
    if "claim_form" in ocr_texts:
        return "claim_field_extractor"
    else:
        return "multi_doc_extractor"

def after_multi_doc(state):
    if state.get("autofill_extracted"):
        return "claim_form_filler"
    return END

def after_extraction(state):
    if state.get("structured_data"):
        return "policy_router"
    return END

def after_policy_router(state):
    # Always go to cross_doc_analyzer; it will skip internally if no extra docs
    return "cross_doc_analyzer"

# def after_audit(state):
#     if state.get("audit_results"):
#         return "report_generator"
#     return END


# ═════════════════════════════════════════════════════════════════════
# Build Graph
# ═════════════════════════════════════════════════════════════════════

def build_claim_validator_graph():
    graph = StateGraph(ClaimValidationState)

    # Shared nodes
    graph.add_node("ocr_extractor", ocr_extractor)

    # Audit path nodes
    graph.add_node("claim_field_extractor", claim_field_extractor)
    graph.add_node("policy_router", policy_router)
    graph.add_node("cross_doc_analyzer", cross_doc_analyzer)
    graph.add_node("claim_auditor", claim_auditor)

    # Autofill path nodes
    graph.add_node("multi_doc_extractor", multi_doc_extractor)
    graph.add_node("claim_form_filler", claim_form_filler)

    # Edges
    graph.add_edge(START, "ocr_extractor")

    # OCR → route to audit or autofill
    graph.add_conditional_edges("ocr_extractor", after_ocr)

    # AUDIT PATH
    graph.add_conditional_edges("claim_field_extractor", after_extraction)
    graph.add_conditional_edges("policy_router", after_policy_router)
    graph.add_edge("cross_doc_analyzer", "claim_auditor")
    graph.add_edge("claim_auditor", END)

    # AUTOFILL PATH
    graph.add_conditional_edges("multi_doc_extractor", after_multi_doc)
    graph.add_edge("claim_form_filler", END)

    return graph.compile()


_compiled_graph = None

def get_claim_validator():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_claim_validator_graph()
    return _compiled_graph


# ═════════════════════════════════════════════════════════════════════
# Public API
# ═════════════════════════════════════════════════════════════════════

def validate_claim(
    documents: Dict[str, bytes],
    filenames: Dict[str, str],
) -> Dict[str, Any]:
    pipeline_start = time.time()
    logger.info(
        f"[CLAIM_GRAPH] Pipeline started: {len(documents)} doc(s) — "
        f"{list(documents.keys())}"
    )

    graph = get_claim_validator()

    initial_state = {
        "documents": documents,
        "filenames": filenames,
        "ocr_texts": {},
        "structured_data": None,
        "policy_baseline": "",
        "policy_type": "",
        "cross_doc_findings": None,
        "thinking_trace": [],
        "audit_results": None,
        "final_report": None,
        "autofill_extracted": None,
        "autofill_result": None,
        "error": None,
        "timings": {},
    }

    final_state = graph.invoke(initial_state)

    elapsed = time.time() - pipeline_start
    logger.info(f"[CLAIM_GRAPH] Pipeline completed in {elapsed:.2f}s")

    return {
        "flow": "audit" if "claim_form" in final_state.get("ocr_texts", {}) else "autofill",
        "structured_data": final_state.get("structured_data"),
        "policy_baseline": final_state.get("policy_baseline"),
        "policy_type": final_state.get("policy_type"),
        "cross_doc_findings": final_state.get("cross_doc_findings"),
        "thinking_trace": final_state.get("thinking_trace", []),
        "audit_results": final_state.get("audit_results"),
        "final_report": final_state.get("final_report"),
        "autofill_extracted": final_state.get("autofill_extracted"),
        "autofill_result": final_state.get("autofill_result"),
        "error": final_state.get("error"),
        "timings": final_state.get("timings", {}),
        "documents_processed": list(final_state.get("ocr_texts", {}).keys()),
    }


# ═════════════════════════════════════════════════════════════════════
# Pipeline 2 Public API — Autofill from pre-OCR'd stored documents
# ═════════════════════════════════════════════════════════════════════

def autofill_from_stored_docs(user_id: str) -> Dict[str, Any]:
    """
    Pipeline 2 — Autofill insurance claim form from pre-OCR'd stored documents.

    Fetches INSURANCE_POLICY, HOSPITAL_BILL, and PRESCRIPTION documents already
    stored in MedilockerDocuments for the given user, reads their pre-computed
    OCR texts from S3, and runs the autofill path (multi_doc_extractor →
    claim_form_filler) without re-running Textract.

    Pipeline 1 (validate_claim) is completely unaffected — this is additive only.

    Returns the same autofill_result shape as validate_claim() but with
    flow="stored_autofill". On failure, returns a dict with "error" and
    "error_code" keys so the caller can map to the right HTTP status.
    """
    from app.services.document_db_service import fetch_stored_ocr_for_user

    pipeline_start = time.time()
    logger.info(
        f"[CLAIM_GRAPH] Stored-doc autofill pipeline started for user_id={user_id}"
    )

    # ── Step 1: Fetch pre-computed OCR texts from DynamoDB / S3 ─────────────
    fetch_result = fetch_stored_ocr_for_user(user_id)

    if "error" in fetch_result:
        error_code = fetch_result["error"]
        if error_code == "missing_docs":
            msg = f"Missing document categories: {fetch_result.get('missing', [])}"
        elif error_code == "ocr_pending":
            msg = f"OCR not yet complete for: {fetch_result.get('pending', [])}"
        else:
            msg = (
                f"Failed to read stored OCR text for category: "
                f"{fetch_result.get('failed_category', 'unknown')}"
            )
        logger.warning(f"[CLAIM_GRAPH] Stored-doc autofill aborted — {msg}")
        return {
            "flow": "stored_autofill",
            "error": msg,
            "error_code": error_code,
            "autofill_extracted": None,
            "autofill_result": None,
        }

    ocr_texts = fetch_result["ocr_texts"]
    logger.info(
        f"[CLAIM_GRAPH] Stored-doc autofill: OCR texts loaded "
        f"({list(ocr_texts.keys())}), running autofill nodes"
    )

    # ── Step 2: Build state and run autofill nodes directly ──────────────────
    # ocr_extractor is intentionally skipped — OCR already done by OCRWorkerLambda.
    state: ClaimValidationState = {
        "documents": {},
        "filenames": {},
        "ocr_texts": ocr_texts,
        "structured_data": None,
        "policy_baseline": "",
        "policy_type": "",
        "cross_doc_findings": None,
        "thinking_trace": [],
        "audit_results": None,
        "final_report": None,
        "autofill_extracted": None,
        "autofill_result": None,
        "error": None,
        "timings": {},
    }

    state.update(multi_doc_extractor(state))
    state.update(claim_form_filler(state))

    elapsed = time.time() - pipeline_start
    logger.info(
        f"[CLAIM_GRAPH] Stored-doc autofill pipeline completed in {elapsed:.2f}s"
    )

    return {
        "flow": "stored_autofill",
        "autofill_extracted": state.get("autofill_extracted"),
        "autofill_result": state.get("autofill_result"),
        "error": state.get("error"),
        "timings": state.get("timings", {}),
        "documents_processed": list(ocr_texts.keys()),
    }