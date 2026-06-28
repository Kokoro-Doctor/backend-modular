"""
Staff router - endpoints for hospital staff to add patients and doctors.

Every request must include hospital_id in the body or form. It must match the
hospital_id in the JWT (Authorization: Bearer ...). hospital_id is never taken
from the request alone for authorization — only after matching the token.
"""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.auth.jwt_auth import assert_hospital_id_matches_token, get_current_hospital
from app.models.schemas import AddDoctorRequest, AddPatientForm, UpdatePatientForm
from app.services.hospital_service import get_hospital_or_raise
from app.services.patient_doc_service import upload_patient_docs, upload_single_doc, INSURANCE_POLICY, HOSPITAL_BILL, PRESCRIPTION
from app.services.staff_excel_import_storage import stage_excel_import
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
    assert_hospital_id_matches_token(data.hospital_id, token_hospital_id)
    did_log = data.doctor_id
    logger.info(
        f"[STAFF] POST /add-patient start hospital_id={data.hospital_id!r} "
        f"doctor_id={did_log!r} name={data.name!r} has_email={bool(data.email)}"
    )
    try:
        hospital = _get_active_hospital(data.hospital_id)
        hname = hospital.get("name") or ""

        if data.doctor_id:
            doctor = get_doctor_for_hospital_staff_patient_flow(data.doctor_id)
            if not is_doctor_in_hospital(data.doctor_id, data.hospital_id):
                logger.warning(
                    f"[STAFF] Doctor {data.doctor_id!r} is not affiliated with hospital "
                    f"{data.hospital_id!r}"
                )
                raise HTTPException(status_code=403, detail="Doctor does not belong to your hospital")
            result = add_patient(
                data.phone,
                data.name,
                data.email,
                hospital_id=data.hospital_id,
                hospital_name=hname,
                doctor_id=doctor["doctor_id"],
                preloaded_doctor=doctor,
                age=data.age,
                gender=data.gender,
                insurer=data.insurer,
            )
        else:
            result = add_patient(
                data.phone,
                data.name,
                data.email,
                hospital_id=data.hospital_id,
                hospital_name=hname,
                doctor_id=None,
                preloaded_doctor=None,
                age=data.age,
                gender=data.gender,
                insurer=data.insurer,
            )

        user_id = (result.get("user") or {}).get("user_id")
        logger.info(
            f"[STAFF] POST /add-patient patient resolved hospital_id={data.hospital_id!r} "
            f"doctor_id={did_log!r} status={result.get('status')} user_id={user_id!r}"
        )

        doc_results = await upload_patient_docs(
            user_id=user_id,
            insurance_policy=data.insurance_policy,
            hospital_bill=data.hospital_bill,
            prescription=data.prescription,
        )

        logger.info(
            f"[STAFF] POST /add-patient docs uploaded hospital_id={data.hospital_id!r} "
            f"user_id={user_id!r} docs={[d['doc_type'] for d in doc_results]}"
        )

        return {**result, "documents": doc_results}

    except HTTPException as e:
        logger.warning(
            f"[STAFF] POST /add-patient failed hospital_id={data.hospital_id!r} "
            f"doctor_id={did_log!r} HTTP {e.status_code}: {e.detail}"
        )
        raise
    except Exception as e:
        logger.exception(
            f"[STAFF] POST /add-patient unexpected error hospital_id={data.hospital_id!r} "
            f"doctor_id={did_log!r}: {e}"
        )
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
        hospital = _get_active_hospital(data.hospital_id)
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
            hospital_name=hospital.get("name") or "",
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
                doc_results.append(await upload_single_doc(data.user_id, doc_type, upload_file))

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


# ---------- Bulk import ----------

@router.post("/import-patients")
def import_patients_endpoint(
    file: UploadFile = File(...),
    doctor_id: str = Form(...),
    hospital_id: str = Form(...),
    token_hospital_id: str = Depends(get_current_hospital),
):
    """Stage patient Excel to S3; rows are processed asynchronously (typically within 24 hours)."""
    hospital_id = (hospital_id or "").strip()
    assert_hospital_id_matches_token(hospital_id, token_hospital_id)
    fname = getattr(file, "filename", None) or "(no filename)"
    logger.info(
        f"[STAFF] POST /import-patients start hospital_id={hospital_id!r} "
        f"doctor_id={doctor_id!r} filename={fname!r} content_type={getattr(file, 'content_type', None)!r}"
    )
    try:
        doctor = get_doctor_for_hospital_staff_patient_flow(doctor_id)

        if not is_doctor_in_hospital(doctor_id, hospital_id):
            logger.warning(
                f"[STAFF] Doctor {doctor_id!r} is not affiliated with hospital {hospital_id!r}"
            )
            raise HTTPException(status_code=403, detail="Doctor does not belong to your hospital")

        hospital = _get_active_hospital(hospital_id)
        content = file.file.read()
        staged = stage_excel_import(
            kind="patient",
            file_bytes=content,
            original_filename=fname if fname != "(no filename)" else "upload.xlsx",
            context={
                "doctor_id": doctor["doctor_id"],
                "hospital_id": hospital_id,
                "content_type": getattr(file, "content_type", None),
            },
        )
        logger.info(
            f"[STAFF] POST /import-patients staged hospital_id={hospital_id!r} "
            f"doctor_id={doctor_id!r} staging_id={staged.get('staging_id')!r}"
        )
        return JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "import_kind": "patient",
                "message": "Your file was received. Data will typically be live within 24 hours.",
                "s3_key": staged["s3_key"],
                "manifest_key": staged["manifest_key"],
                "staging_id": staged["staging_id"],
                "eta_hours": 24,
            },
        )
    except HTTPException as e:
        logger.warning(
            f"[STAFF] POST /import-patients failed hospital_id={hospital_id!r} "
            f"doctor_id={doctor_id!r} filename={fname!r} HTTP {e.status_code}: {e.detail}"
        )
        raise
    except Exception as e:
        logger.exception(
            f"[STAFF] POST /import-patients unexpected error hospital_id={hospital_id!r} "
            f"doctor_id={doctor_id!r} filename={fname!r}: {e}"
        )
        handle_exception(e, "Import patients")


@router.post("/import-doctors")
def import_doctors_endpoint(
    file: UploadFile = File(...),
    hospital_id: str = Form(...),
    token_hospital_id: str = Depends(get_current_hospital),
):
    """Stage doctor Excel to S3 — hospital_id in form must match JWT."""
    hospital_id = (hospital_id or "").strip()
    assert_hospital_id_matches_token(hospital_id, token_hospital_id)
    fname = getattr(file, "filename", None) or "(no filename)"
    logger.info(
        f"[STAFF] POST /import-doctors start hospital_id={hospital_id!r} "
        f"filename={fname!r} content_type={getattr(file, 'content_type', None)!r}"
    )
    try:
        hospital = _get_active_hospital(hospital_id)
        content = file.file.read()
        staged = stage_excel_import(
            kind="doctor",
            file_bytes=content,
            original_filename=fname if fname != "(no filename)" else "upload.xlsx",
            context={
                "hospital_id": hospital_id,
                "hospital_name": hospital.get("name", ""),
                "content_type": getattr(file, "content_type", None),
            },
        )
        logger.info(
            f"[STAFF] POST /import-doctors staged hospital_id={hospital_id!r} "
            f"staging_id={staged.get('staging_id')!r}"
        )
        return JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "import_kind": "doctor",
                "message": "Your file was received. Data will typically be live within 24 hours.",
                "s3_key": staged["s3_key"],
                "manifest_key": staged["manifest_key"],
                "staging_id": staged["staging_id"],
                "eta_hours": 24,
            },
        )
    except HTTPException as e:
        logger.warning(
            f"[STAFF] POST /import-doctors failed hospital_id={hospital_id!r} "
            f"filename={fname!r} HTTP {e.status_code}: {e.detail}"
        )
        raise
    except Exception as e:
        logger.exception(
            f"[STAFF] POST /import-doctors unexpected error hospital_id={hospital_id!r} filename={fname!r}: {e}"
        )
        handle_exception(e, "Import doctors")
