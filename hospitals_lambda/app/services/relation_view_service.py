"""
Read models for user-doctor-hospital relation APIs.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import HTTPException

from app.config import DOCTORS_TABLE, USERS_TABLE
from app.logger import get_logger
from app.services.user_doctor_service import (
    LinkedBy,
    RelationType,
    create_relation,
    deactivate_relation,
    get_doctor_patients,
    get_relation_for_pair,
    get_user_doctors,
)
from app.services.membership_service import (
    is_doctor_in_hospital,
    list_doctor_ids_for_hospital,
    list_user_ids_for_hospital,
)

logger = get_logger(__name__)

PATIENT_RESPONSE_FIELDS = ("name", "phoneNumber", "user_id", "gender", "age", "createdAt")
SENSITIVE_PATIENT_RESPONSE_FIELDS = {
    "password",
    "password_hash",
    "hashed_password",
    "otp",
    "otp_hash",
    "otp_expiry",
    "otp_attempts",
    "reset_token",
    "refresh_token",
    "access_token",
}


def _get_user(user_id: str) -> Optional[dict]:
    try:
        resp = USERS_TABLE.get_item(Key={"user_id": user_id})
        return resp.get("Item")
    except Exception as e:
        logger.error(f"[relation_view] Failed to load user user_id={user_id!r}: {e!r}")
        raise HTTPException(status_code=500, detail="Failed to load user")


def _get_doctor(doctor_id: str) -> Optional[dict]:
    try:
        resp = DOCTORS_TABLE.get_item(Key={"doctor_id": doctor_id})
        return resp.get("Item")
    except Exception as e:
        logger.error(f"[relation_view] Failed to load doctor doctor_id={doctor_id!r}: {e!r}")
        raise HTTPException(status_code=500, detail="Failed to load doctor")


def _patient_summary(user: Optional[dict], fallback_user_id: str) -> dict:
    user = user or {}
    summary = {
        field: value
        for field, value in user.items()
        if field not in SENSITIVE_PATIENT_RESPONSE_FIELDS
        and not str(field).startswith("_")
    }
    for field in PATIENT_RESPONSE_FIELDS:
        summary.setdefault(field, user.get(field))
    summary["user_id"] = summary.get("user_id") or fallback_user_id
    return summary


def list_user_doctors(user_id: str) -> dict:
    relations = get_user_doctors(user_id)
    rows = []
    for relation in relations:
        doctor_id = relation.get("doctor_id")
        rows.append({
            "doctor": _get_doctor(doctor_id) if doctor_id else None,
            "relation": relation,
        })
    return {"user_id": user_id, "doctors": rows, "count": len(rows)}


def get_user_doctor_relation(user_id: str, doctor_id: str) -> dict:
    relation = get_relation_for_pair(user_id, doctor_id, active_only=True)
    return {
        "user_id": user_id,
        "doctor_id": doctor_id,
        "exists": relation is not None,
        "relation": relation,
    }


def remove_user_doctor(user_id: str, doctor_id: str) -> dict:
    relation = deactivate_relation(user_id, doctor_id)
    return {
        "user_id": user_id,
        "doctor_id": doctor_id,
        "relation": relation,
        "message": "Doctor removed from user",
    }


def list_doctor_patients(doctor_id: str) -> dict:
    relations = get_doctor_patients(doctor_id)
    rows = []
    for relation in relations:
        user_id = relation.get("user_id")
        rows.append({
            "user": _get_user(user_id) if user_id else None,
            "relation": relation,
        })
    return {"doctor_id": doctor_id, "patients": rows, "count": len(rows)}


def count_doctor_patients(doctor_id: str) -> dict:
    relations = get_doctor_patients(doctor_id)
    unique_user_ids = {r.get("user_id") for r in relations if r.get("user_id")}
    return {"doctor_id": doctor_id, "count": len(unique_user_ids)}


def list_hospital_patients(hospital_id: str) -> dict:
    """All patients affiliated with a hospital via the UserHospital junction.

    Includes hospital-only patients (those with no doctor relation), which the
    old relation-derived listing missed.
    """
    user_ids = list_user_ids_for_hospital(hospital_id)
    patients = [_patient_summary(_get_user(uid), uid) for uid in user_ids]
    return {"patients": patients}


# Back-compat alias: the router historically imported this name.
list_hospital_patients_from_relations = list_hospital_patients


def _relations_for_hospital(hospital_id: str) -> List[dict]:
    """Active user-doctor bonds whose origin hospital is this hospital.

    Derived from the hospital's doctors (DoctorHospital) → each doctor's active
    patients (UserDoctor), filtered to bonds assigned at this hospital. Avoids a
    table scan.
    """
    hid = (hospital_id or "").strip()
    relations: List[dict] = []
    for doctor_id in list_doctor_ids_for_hospital(hid):
        for rel in get_doctor_patients(doctor_id):
            if rel.get("hospital_id") == hid:
                relations.append(rel)
    return relations


def list_hospital_doctors_with_patient_counts(hospital_id: str) -> dict:
    doctor_ids = list_doctor_ids_for_hospital(hospital_id)
    relations = _relations_for_hospital(hospital_id)
    patients_by_doctor: Dict[str, set] = {}
    for relation in relations:
        doctor_id = relation.get("doctor_id")
        user_id = relation.get("user_id")
        if doctor_id and user_id:
            patients_by_doctor.setdefault(doctor_id, set()).add(user_id)
    rows = [
        {
            "doctor": _get_doctor(doctor_id),
            "patient_count": len(patients_by_doctor.get(doctor_id, set())),
        }
        for doctor_id in doctor_ids
    ]
    return {"hospital_id": hospital_id, "doctors": rows, "count": len(rows)}


def list_hospital_relations(hospital_id: str) -> dict:
    relations = _relations_for_hospital(hospital_id)
    return {"hospital_id": hospital_id, "relations": relations, "count": len(relations)}


def create_hospital_relation(
    *,
    user_id: str,
    doctor_id: str,
    hospital_id: str,
    relation_type: str,
    linked_by: str,
) -> dict:
    if relation_type not in {
        RelationType.HOSPITAL_ASSIGNED,
        RelationType.MANUAL,
        RelationType.SUBSCRIPTION,
    }:
        raise HTTPException(status_code=400, detail="Invalid relation_type")

    if linked_by not in {LinkedBy.HOSPITAL_STAFF, LinkedBy.DOCTOR, LinkedBy.SYSTEM}:
        raise HTTPException(status_code=400, detail="Invalid linked_by")

    user = _get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    doctor = _get_doctor(doctor_id)
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    if not is_doctor_in_hospital(doctor_id, hospital_id):
        raise HTTPException(status_code=403, detail="Doctor does not belong to this hospital")

    relation = create_relation(
        user_id=user_id,
        doctor_id=doctor_id,
        relation_type=relation_type,
        linked_by=linked_by,
        hospital_id=hospital_id,
    )
    return {"relation": relation}
