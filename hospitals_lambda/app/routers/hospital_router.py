"""
Hospital router - hospital CRUD endpoints and login.
"""
from fastapi import APIRouter, Body, Depends, HTTPException

from app.auth.jwt_auth import assert_hospital_id_matches_token, get_current_hospital
from app.config import JWT_EXPIRE_HOURS
from app.models.schemas import (
    DiagnosisSummaryResponse,
    HospitalCreate,
    HospitalIdBody,
    HospitalLoginRequest,
    HospitalLoginResponse,
    HospitalUpdate,
    RelationCreateRequest,
)
from app.services.hospital_service import (
    create_hospital,
    list_hospitals,
    get_hospital_or_raise,
    update_hospital,
    disable_hospital,
    validate_hospital_login,
)
from app.services.user_diagnosis_service import get_user_diagnosis_summary
from app.services.relation_view_service import (
    count_doctor_patients,
    create_hospital_relation,
    get_user_doctor_relation,
    list_doctor_patients,
    list_hospital_doctors_with_patient_counts,
    list_hospital_patients_from_relations,
    list_hospital_relations,
    list_user_doctors,
    remove_user_doctor,
)
from app.utils.error_utils import handle_exception
from app.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/hospitals", tags=["Hospitals"])


def _assert_path_body_hospital_match(path_hospital_id: str, body_hospital_id: str) -> None:
    p = (path_hospital_id or "").strip()
    b = (body_hospital_id or "").strip()
    if p != b:
        raise HTTPException(
            status_code=400,
            detail="hospital_id in URL path and request body must match",
        )


@router.post("/signup", status_code=201)
def create_hospital_endpoint(data: HospitalCreate):
    """Register a new hospital with email/mobile and password."""
    logger.info(f"[HOSPITALS] POST /signup received, name={data.name}")
    try:
        hospital = create_hospital(data.model_dump())
        logger.info(f"[HOSPITALS] POST /signup success, hospital_id={hospital.get('hospital_id')}")
        return {"hospital": hospital}
    except Exception as e:
        logger.exception(f"[HOSPITALS] POST /signup failed: {e}")
        handle_exception(e, "Create hospital")


@router.post("/login", response_model=HospitalLoginResponse)
def hospital_login_endpoint(data: HospitalLoginRequest):
    """Authenticate a hospital using email or contact number and password. Returns JWT token."""
    logger.info(f"[HOSPITALS] POST /login received, identifier={data.identifier!r}")
    try:
        hospital, token = validate_hospital_login(
            identifier=data.identifier,
            password=data.password,
        )
        hospital_id = hospital.get("hospital_id")
        logger.info(f"[HOSPITALS] POST /login success for hospital_id={hospital_id}")
        return {
            "hospital": hospital,
            "token": token,
            "expires_in": JWT_EXPIRE_HOURS * 3600,
            "message": "Login successful",
        }
    except HTTPException:
        logger.warning(f"[HOSPITALS] POST /login failed for identifier={data.identifier!r}")
        raise
    except Exception as e:
        logger.exception(f"[HOSPITALS] POST /login error: {e}")
        handle_exception(e, "Hospital login")


@router.get("/list")
def list_hospitals_endpoint():
    """List all active hospitals (no request body — GET should be safe and cacheable)."""
    logger.info("[HOSPITALS] GET /list received")
    try:
        hospitals = list_hospitals(active_only=True)
        logger.info(f"[HOSPITALS] GET /list success, count={len(hospitals)}")
        return {"hospitals": hospitals}
    except Exception as e:
        logger.exception(f"[HOSPITALS] GET /list failed: {e}")
        handle_exception(e, "List hospitals")


@router.get("/users/{user_id}/doctors")
def list_user_doctors_endpoint(user_id: str):
    """Get all active doctors linked to a user."""
    logger.info(f"[HOSPITALS] GET /users/{user_id}/doctors received")
    try:
        return list_user_doctors((user_id or "").strip())
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[HOSPITALS] GET /users/{user_id}/doctors failed: {e}")
        handle_exception(e, "List user doctors")


@router.get(
    "/users/{user_id}/diagnosis-summary",
    response_model=DiagnosisSummaryResponse,
)
def get_user_diagnosis_summary_endpoint(user_id: str):
    """Get diagnosis fields stored on the user's profile."""
    uid = (user_id or "").strip()
    logger.info("[HOSPITALS] GET /users/%s/diagnosis-summary received", uid)
    try:
        return get_user_diagnosis_summary(uid)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "[HOSPITALS] GET /users/%s/diagnosis-summary failed: %s",
            uid,
            e,
        )
        handle_exception(e, "Get user diagnosis summary")


@router.get("/users/{user_id}/doctors/{doctor_id}/relation")
def get_user_doctor_relation_endpoint(user_id: str, doctor_id: str):
    """Check whether a user is linked to a specific doctor."""
    logger.info(f"[HOSPITALS] GET /users/{user_id}/doctors/{doctor_id}/relation received")
    try:
        return get_user_doctor_relation(
            (user_id or "").strip(),
            (doctor_id or "").strip(),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[HOSPITALS] GET /relation failed user_id={user_id!r} doctor_id={doctor_id!r}: {e}")
        handle_exception(e, "Get user doctor relation")


@router.delete("/users/{user_id}/doctors/{doctor_id}")
def remove_user_doctor_endpoint(user_id: str, doctor_id: str):
    """Remove a doctor from a user by setting the active relation status to INACTIVE."""
    logger.info(f"[HOSPITALS] DELETE /users/{user_id}/doctors/{doctor_id} received")
    try:
        return remove_user_doctor(
            (user_id or "").strip(),
            (doctor_id or "").strip(),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[HOSPITALS] DELETE /users/{user_id}/doctors/{doctor_id} failed: {e}")
        handle_exception(e, "Remove user doctor")


@router.get("/doctors/{doctor_id}/patients")
def list_doctor_patients_endpoint(doctor_id: str):
    """Get all active patients assigned to a doctor."""
    logger.info(f"[HOSPITALS] GET /doctors/{doctor_id}/patients received")
    try:
        return list_doctor_patients((doctor_id or "").strip())
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[HOSPITALS] GET /doctors/{doctor_id}/patients failed: {e}")
        handle_exception(e, "List doctor patients")


@router.get("/doctors/{doctor_id}/patients/count")
def count_doctor_patients_endpoint(doctor_id: str):
    """Get unique active patient count for a doctor."""
    logger.info(f"[HOSPITALS] GET /doctors/{doctor_id}/patients/count received")
    try:
        return count_doctor_patients((doctor_id or "").strip())
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[HOSPITALS] GET /doctors/{doctor_id}/patients/count failed: {e}")
        handle_exception(e, "Count doctor patients")


@router.post("/relations", status_code=201)
def create_hospital_relation_endpoint(
    data: RelationCreateRequest,
    token_hospital_id: str = Depends(get_current_hospital),
):
    """Create or reactivate a user-doctor relation for a hospital."""
    assert_hospital_id_matches_token(data.hospital_id, token_hospital_id)
    logger.info(
        f"[HOSPITALS] POST /relations received hospital_id={data.hospital_id!r} "
        f"user_id={data.user_id!r} doctor_id={data.doctor_id!r}"
    )
    try:
        hospital = get_hospital_or_raise(data.hospital_id)
        if not hospital.get("is_active", True):
            raise HTTPException(status_code=403, detail="Hospital account is disabled")
        return create_hospital_relation(**data.model_dump())
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[HOSPITALS] POST /relations failed: {e}")
        handle_exception(e, "Create hospital relation")


@router.get("/{hospital_id}/patients")
def list_hospital_patients_endpoint(
    hospital_id: str,
    token_hospital_id: str = Depends(get_current_hospital),
):
    """
    List unique users assigned through this hospital's active doctor-patient relations.
    Requires Bearer token from POST /hospitals/login.
    """
    hid = (hospital_id or "").strip()
    assert_hospital_id_matches_token(hid, token_hospital_id)
    logger.info(f"[HOSPITALS] GET /{{hospital_id}}/patients hospital_id={hid!r}")
    try:
        hospital = get_hospital_or_raise(hid)
        if not hospital.get("is_active", True):
            raise HTTPException(status_code=403, detail="Hospital account is disabled")
        return list_hospital_patients_from_relations(hid)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[HOSPITALS] GET /patients failed hospital_id={hid!r}: {e}")
        handle_exception(e, "List hospital patients")


@router.get("/{hospital_id}/doctors")
def list_hospital_doctors_endpoint(
    hospital_id: str,
    token_hospital_id: str = Depends(get_current_hospital),
):
    """Get all doctors in a hospital with active assigned patient counts."""
    hid = (hospital_id or "").strip()
    assert_hospital_id_matches_token(hid, token_hospital_id)
    logger.info(f"[HOSPITALS] GET /{{hospital_id}}/doctors hospital_id={hid!r}")
    try:
        hospital = get_hospital_or_raise(hid)
        if not hospital.get("is_active", True):
            raise HTTPException(status_code=403, detail="Hospital account is disabled")
        return list_hospital_doctors_with_patient_counts(hid)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[HOSPITALS] GET /doctors failed hospital_id={hid!r}: {e}")
        handle_exception(e, "List hospital doctors")


@router.get("/{hospital_id}/relations")
def list_hospital_relations_endpoint(
    hospital_id: str,
    token_hospital_id: str = Depends(get_current_hospital),
):
    """Get all active doctor-patient assignments for a hospital."""
    hid = (hospital_id or "").strip()
    assert_hospital_id_matches_token(hid, token_hospital_id)
    logger.info(f"[HOSPITALS] GET /{{hospital_id}}/relations hospital_id={hid!r}")
    try:
        hospital = get_hospital_or_raise(hid)
        if not hospital.get("is_active", True):
            raise HTTPException(status_code=403, detail="Hospital account is disabled")
        return list_hospital_relations(hid)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[HOSPITALS] GET /relations failed hospital_id={hid!r}: {e}")
        handle_exception(e, "List hospital relations")


@router.get("/get/{hospital_id}")
def get_hospital_endpoint(
    hospital_id: str,
    body: HospitalIdBody = Body(...),
):
    """Get hospital by ID (path and body hospital_id must match)."""
    _assert_path_body_hospital_match(hospital_id, body.hospital_id)
    logger.info(f"[HOSPITALS] GET /get/{hospital_id} received")
    try:
        hospital = get_hospital_or_raise(hospital_id)
        logger.info(f"[HOSPITALS] GET /get/{hospital_id} success")
        return {"hospital": hospital}
    except HTTPException:
        logger.warning(f"[HOSPITALS] GET /get/{hospital_id} not found or error")
        raise
    except Exception as e:
        logger.exception(f"[HOSPITALS] GET /get/{hospital_id} failed: {e}")
        handle_exception(e, "Get hospital")


@router.put("/update/{hospital_id}")
def update_hospital_endpoint(
    hospital_id: str,
    data: HospitalUpdate,
):
    """Update hospital metadata (path and body hospital_id must match)."""
    _assert_path_body_hospital_match(hospital_id, data.hospital_id)
    logger.info(f"[HOSPITALS] PUT /update/{hospital_id} received")
    try:
        update_data = {k: v for k, v in data.model_dump().items() if v is not None and k != "hospital_id"}
        hospital = update_hospital(hospital_id, update_data)
        logger.info(f"[HOSPITALS] PUT /update/{hospital_id} success")
        return {"hospital": hospital}
    except HTTPException:
        logger.warning(f"[HOSPITALS] PUT /update/{hospital_id} failed (HTTPException)")
        raise
    except Exception as e:
        logger.exception(f"[HOSPITALS] PUT /update/{hospital_id} failed: {e}")
        handle_exception(e, "Update hospital")


@router.put("/disable/{hospital_id}")
def disable_hospital_endpoint(
    hospital_id: str,
    body: HospitalIdBody = Body(...),
):
    """Soft delete hospital - set is_active = false (path and body hospital_id must match)."""
    _assert_path_body_hospital_match(hospital_id, body.hospital_id)
    logger.info(f"[HOSPITALS] PUT /disable/{hospital_id} received")
    try:
        hospital = disable_hospital(hospital_id)
        logger.info(f"[HOSPITALS] PUT /disable/{hospital_id} success")
        return {"hospital": hospital}
    except HTTPException:
        logger.warning(f"[HOSPITALS] PUT /disable/{hospital_id} failed (HTTPException)")
        raise
    except Exception as e:
        logger.exception(f"[HOSPITALS] PUT /disable/{hospital_id} failed: {e}")
        handle_exception(e, "Disable hospital")
