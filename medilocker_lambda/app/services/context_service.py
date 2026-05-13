"""
Context service - builds patient context from stored documents.

Pure context-building logic with no GPT calls.
Used by prescription, clinical query, and extraction flows.
"""
import json
from typing import List, Dict, Any

from app.logger import get_logger

logger = get_logger(__name__)


def build_patient_context(documents: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build chronological patient medical context from stored documents.

    This preserves full document-level medical history instead of merging data.

    Args:
        documents: DynamoDB records containing structured_data

    Returns:
        patient_context dict
    """
    context = {
        "patient_summary": {
            "name": None,
            "age": None,
            "gender": None,
        },
        "document_history": [],
    }

    for doc in documents:
        structured = doc.get("structured_data")
        if not structured:
            continue

        if isinstance(structured, str):
            try:
                structured = json.loads(structured)
            except Exception:
                continue

        patient = structured.get("patient_details", {})

        if patient:
            if patient.get("name"):
                context["patient_summary"]["name"] = patient["name"]
            if patient.get("age"):
                context["patient_summary"]["age"] = patient["age"]
            if patient.get("gender"):
                context["patient_summary"]["gender"] = patient["gender"]

        context["document_history"].append({
            "created_at": doc.get("created_at"),
            "document_category": doc.get("document_category"),
            "file_id": doc.get("file_id"),
            "data": structured,
        })

    context["document_history"].sort(
        key=lambda x: x["created_at"] or "",
        reverse=True,
    )

    logger.info(
        f"[PATIENT_CONTEXT] Built context with "
        f"{len(context['document_history'])} documents"
    )

    return context


def documents_from_extracted_data(
    extracted_data_list: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Convert in-memory extracted data to document format for build_patient_context.

    Args:
        extracted_data_list: List of structured data dicts from GPT extraction

    Returns:
        List of document dicts with structured_data, created_at, document_category, file_id
    """
    return [
        {
            "structured_data": data,
            "created_at": None,
            "document_category": data.get("document_category"),
            "file_id": None,
        }
        for data in extracted_data_list
    ]


def extract_patient_details_from_context(patient_context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract patient summary and primary diagnosis from patient context.

    Used when building prescription/clinical response payloads.

    Args:
        patient_context: Output of build_patient_context()

    Returns:
        Dict with name, age, gender, diagnosis (values may be None)
    """
    patient_summary = patient_context.get("patient_summary", {})
    primary_diagnosis = None
    for doc_entry in patient_context.get("document_history", []):
        diagnoses = doc_entry.get("data", {}).get("diagnoses", [])
        if diagnoses:
            primary_diagnosis = diagnoses[0]
            break

    return {
        "name": patient_summary.get("name"),
        "age": patient_summary.get("age"),
        "gender": patient_summary.get("gender"),
        "diagnosis": primary_diagnosis,
    }
