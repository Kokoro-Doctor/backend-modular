"""
Staff router - endpoints for hospital staff to add patients and doctors.

Hospital identity comes from the JWT for add-patient. Endpoints that still
accept hospital_id explicitly validate it against the JWT before using it.
"""
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.auth.jwt_auth import assert_hospital_id_matches_token, get_current_hospital
from app.models.schemas import AddDoctorRequest, AddPatientForm, UpdatePatientForm
from app.services.hospital_service import get_hospital_or_raise
from app.services.patient_doc_service import (
    upload_patient_docs,
    upload_single_doc,
    upload_documents_for_patient,
    list_patient_docs_for_hospital,
    list_documents_for_hospital,
    INSURANCE_POLICY,
    HOSPITAL_BILL,
    PRESCRIPTION,
)
from app.services.staff_service import (
    add_patient,
    add_doctor,
    get_doctor_for_hospital_staff_patient_flow,
    update_patient,
)
from app.services.membership_service import is_doctor_in_hospital
from app.utils.error_utils import handle_exception
from app.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/hospitals/staff", tags=["Hospital Staff"])


def _get_active_hospital(hospital_id: str) -> dict:
    """Fetch the hospital record and verify it is active."""
    logger.info(f"[STAFF] Resolving hospital hospital_id={hospital_id!r}")
    hospital = get_hospital_or_raise(hospital_id)
    if not hospital.get("is_active", True):
        logger.warning(
            f"[STAFF] Hospital disabled hospital_id={hospital_id!r} name={hospital.get('name')!r}"
        )
        raise HTTPException(status_code=403, detail="Hospital account is disabled")
    logger.info(
        f"[STAFF] Hospital OK hospital_id={hospital.get('hospital_id')!r} "
        f"name={hospital.get('name')!r}"
    )
    return hospital


# ---------- Single add ----------


@router.post("/add-patient", status_code=201)
async def add_patient_endpoint(
    data: AddPatientForm = Depends(),
    token_hospital_id: str = Depends(get_current_hospital),
):
    """Add a patient with 3 mandatory documents (insurance policy, hospital bill, prescription).
    Documents are stored on S3 and OCR is run asynchronously in the background."""

    hospital_id = token_hospital_id
    doctor_id = data.doctor_id or None
    hospital = _get_active_hospital(hospital_id)
    hname = hospital.get("name") or ""

    try:
        result = add_patient(
            data.phone,
            data.name,
            data.email or None,
            hospital_id=hospital_id,
            hospital_name=hname,
            doctor_id=doctor_id,
            age=data.age,
            gender=data.gender or None,
            insurer=data.insurer or None,
        )

        user_id = (result.get("user") or {}).get("user_id")
        logger.info(
            f"[STAFF] POST /add-patient patient resolved hospital_id={hospital_id!r} "
            f"doctor_id={doctor_id!r} status={result.get('status')} user_id={user_id!r}"
        )

        doc_results = await upload_patient_docs(
            user_id=user_id,
            insurance_policy=data.insurance_policy,
            hospital_bill=data.hospital_bill,
            prescription=data.prescription,
            hospital_id=hospital_id,
        )

        logger.info(
            f"[STAFF] POST /add-patient docs uploaded hospital_id={hospital_id!r} "
        )

        return {**result, "documents": doc_results}

    except Exception as e:
        handle_exception(e, "Add patient")


@router.post("/update_patient")
async def update_patient_endpoint(
    data: UpdatePatientForm = Depends(),
    token_hospital_id: str = Depends(get_current_hospital),
):
    """Partially update an existing hospital patient.

    All fields are optional except hospital_id and user_id. Documents (insurance_policy,
    hospital_bill, prescription) can be supplied as optional file uploads to add new versions.
    If doctor_id is given it replaces the current hospital-assigned doctor (same id → no-op).
    """
    assert_hospital_id_matches_token(data.hospital_id, token_hospital_id)
    logger.info(
        f"[STAFF] POST /update_patient start hospital_id={data.hospital_id!r} "
        f"user_id={data.user_id!r} doctor_id={data.doctor_id!r}"
    )
    try:
        _get_active_hospital(data.hospital_id)
        doctor = None
        if data.doctor_id:
            doctor = get_doctor_for_hospital_staff_patient_flow(data.doctor_id)
            if not is_doctor_in_hospital(data.doctor_id, data.hospital_id):
                logger.warning(
                    f"[STAFF] Doctor {data.doctor_id!r} is not affiliated with hospital "
                    f"{data.hospital_id!r}"
                )
                raise HTTPException(status_code=403, detail="Doctor does not belong to your hospital")

        result = update_patient(
            hospital_id=data.hospital_id,
            user_id=data.user_id,
            doctor_id=data.doctor_id,
            preloaded_doctor=doctor,
            updates=data.scalar_updates(),
        )

        doc_results = []
        for doc_type, upload_file in [
            (INSURANCE_POLICY, data.insurance_policy),
            (HOSPITAL_BILL, data.hospital_bill),
            (PRESCRIPTION, data.prescription),
        ]:
            if upload_file and getattr(upload_file, "filename", None):
                doc_results.append(
                    await upload_single_doc(
                        data.user_id, doc_type, upload_file, data.hospital_id
                    )
                )

        if doc_results:
            result["documents"] = doc_results

        logger.info(
            f"[STAFF] POST /update_patient success hospital_id={data.hospital_id!r} "
            f"user_id={(result.get('user') or {}).get('user_id')!r} "
            f"fields={result.get('updated_fields')} doctor_action={result.get('doctor_action')} "
            f"docs_uploaded={[d['doc_type'] for d in doc_results]}"
        )
        return result
    except HTTPException as e:
        logger.warning(
            f"[STAFF] POST /update_patient failed hospital_id={data.hospital_id!r} "
            f"user_id={data.user_id!r} HTTP {e.status_code}: {e.detail}"
        )
        raise
    except Exception as e:
        logger.exception(
            f"[STAFF] POST /update_patient unexpected error hospital_id={data.hospital_id!r} "
            f"user_id={data.user_id!r}: {e}"
        )
        handle_exception(e, "Update patient")


@router.get("/patients/{user_id}/documents")
def list_patient_documents_endpoint(
    user_id: str,
    token_hospital_id: str = Depends(get_current_hospital),
):
    """List a patient's documents visible to the calling hospital.

    Returns the patient's own uploads plus the documents *this* hospital
    uploaded for the patient. Documents uploaded by other hospitals are never
    returned. The hospital is taken from the JWT, not the request.
    """
    logger.info(
        f"[STAFF] GET /patients/{user_id}/documents hospital_id={token_hospital_id!r}"
    )
    try:
        documents = list_patient_docs_for_hospital(user_id, token_hospital_id)
        return {"user_id": user_id, "count": len(documents), "documents": documents}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            f"[STAFF] GET /patients/{user_id}/documents failed "
            f"hospital_id={token_hospital_id!r}: {e}"
        )
        handle_exception(e, "List patient documents")


@router.post("/patients/{user_id}/documents", status_code=201)
async def upload_patient_documents_endpoint(
    user_id: str,
    files: List[UploadFile] = File(...),
    metadata: Optional[str] = Form(
        None,
        description='JSON object keyed by filename, e.g. {"scan.pdf": {"doc_type": "LAB_REPORT"}}',
    ),
    token_hospital_id: str = Depends(get_current_hospital),
):
    """Attach one or more documents to an existing patient.

    Unlike /add-patient and /update_patient, this doesn't require the full
    patient form — just files for an existing user_id. doc_type is optional
    per file (defaults to OTHER) and is not restricted to the 3 admission
    document types. hospital_id is taken from the JWT and recorded as the
    upload source, same as every other staff upload path.
    """
    metadata_map = {}
    if metadata:
        try:
            metadata_map = json.loads(metadata)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="metadata must be valid JSON")
        if not isinstance(metadata_map, dict):
            raise HTTPException(status_code=400, detail="metadata must be a JSON object keyed by filename")

    logger.info(
        f"[STAFF] POST /patients/{user_id}/documents hospital_id={token_hospital_id!r} "
        f"file_count={len(files)}"
    )
    try:
        documents = await upload_documents_for_patient(
            user_id, token_hospital_id, files, metadata_map
        )
        return {"user_id": user_id, "documents": documents}
    except HTTPException as e:
        logger.warning(
            f"[STAFF] POST /patients/{user_id}/documents failed "
            f"hospital_id={token_hospital_id!r} HTTP {e.status_code}: {e.detail}"
        )
        raise
    except Exception as e:
        logger.exception(
            f"[STAFF] POST /patients/{user_id}/documents unexpected error "
            f"hospital_id={token_hospital_id!r}: {e}"
        )
        handle_exception(e, "Upload patient documents")


@router.get("/documents")
def list_hospital_documents_endpoint(
    token_hospital_id: str = Depends(get_current_hospital),
):
    """List every document this hospital has uploaded, across all patients.

    Dashboard view — unlike GET /patients/{user_id}/documents (scoped to one
    patient, includes that patient's own uploads), this returns only
    source=HOSPITAL documents uploaded by the calling hospital. hospital_id is
    taken from the JWT, not the request.
    """
    logger.info(f"[STAFF] GET /documents hospital_id={token_hospital_id!r}")
    try:
        documents = list_documents_for_hospital(token_hospital_id)
        return {"hospital_id": token_hospital_id, "count": len(documents), "documents": documents}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            f"[STAFF] GET /documents failed hospital_id={token_hospital_id!r}: {e}"
        )
        handle_exception(e, "List hospital documents")


@router.post("/add-doctor", status_code=201)
def add_doctor_endpoint(
    data: AddDoctorRequest,
    token_hospital_id: str = Depends(get_current_hospital),
):
    """Add a single doctor — hospital_id in body must match JWT."""
    assert_hospital_id_matches_token(data.hospital_id, token_hospital_id)
    hospital_id = data.hospital_id
    logger.info(
        f"[STAFF] POST /add-doctor start hospital_id={hospital_id!r} "
        f"name={data.name!r} specialization={data.specialization!r} has_email={bool(data.email)}"
    )
    try:
        hospital = _get_active_hospital(hospital_id)
        result = add_doctor(
            hospital_id=hospital_id,
            hospital_name=hospital.get("name", ""),
            phone=data.phone,
            name=data.name,
            specialization=data.specialization,
            experience=data.experience,
            email=data.email,
        )
        logger.info(
            f"[STAFF] POST /add-doctor success hospital_id={hospital_id!r} "
            f"status={result.get('status')} doctor_id="
            f"{(result.get('doctor') or {}).get('doctor_id')!r}"
        )
        return result
    except HTTPException as e:
        logger.warning(
            f"[STAFF] POST /add-doctor failed hospital_id={hospital_id!r} "
            f"HTTP {e.status_code}: {e.detail}"
        )
        raise
    except Exception as e:
        logger.exception(
            f"[STAFF] POST /add-doctor unexpected error hospital_id={hospital_id!r}: {e}"
        )
        handle_exception(e, "Add doctor")
