"""
Read models for user-doctor-hospital relation APIs.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from boto3.dynamodb.conditions import Attr
from fastapi import HTTPException

from app.config import DOCTORS_TABLE, USERS_TABLE
from app.logger import get_logger
from app.services.user_doctor_relation_service import (
    LinkedBy,
    RelationType,
    create_relation,
    deactivate_relation,
    get_doctor_patients,
    get_relation_for_pair,
    get_user_doctors,
    list_active_relations_for_hospital,
)

logger = get_logger(__name__)

PATIENT_RESPONSE_FIELDS = ("name", "phoneNumber", "user_id", "gender", "age", "createdAt")


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


def _scan_doctors_for_hospital(hospital_id: str) -> List[dict]:
    doctors: List[dict] = []
    try:
        eks = None
        while True:
            kwargs: Dict[str, Any] = {
                "FilterExpression": Attr("hospital_id").eq(hospital_id),
            }
            if eks:
                kwargs["ExclusiveStartKey"] = eks
            resp = DOCTORS_TABLE.scan(**kwargs)
            doctors.extend(resp.get("Items", []))
            eks = resp.get("LastEvaluatedKey")
            if not eks:
                break
        return doctors
    except Exception as e:
        logger.error(f"[relation_view] Failed to scan doctors hospital_id={hospital_id!r}: {e!r}")
        raise HTTPException(status_code=500, detail="Failed to list hospital doctors")


def _patient_summary(user: Optional[dict], fallback_user_id: str) -> dict:
    user = user or {}
    summary = {field: user.get(field) for field in PATIENT_RESPONSE_FIELDS}
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


def list_hospital_patients_from_relations(hospital_id: str) -> dict:
    relations = list_active_relations_for_hospital(hospital_id)
    by_user: Dict[str, List[dict]] = {}
    for relation in relations:
        user_id = relation.get("user_id")
        if user_id:
            by_user.setdefault(user_id, []).append(relation)

    patients = []
    for user_id in by_user:
        patients.append(_patient_summary(_get_user(user_id), user_id))
    return {"patients": patients}


def list_hospital_doctors_with_patient_counts(hospital_id: str) -> dict:
    doctors = _scan_doctors_for_hospital(hospital_id)
    relations = list_active_relations_for_hospital(hospital_id)
    patients_by_doctor: Dict[str, set[str]] = {}
    for relation in relations:
        doctor_id = relation.get("doctor_id")
        user_id = relation.get("user_id")
        if doctor_id and user_id:
            patients_by_doctor.setdefault(doctor_id, set()).add(user_id)
    rows = [
        {
            "doctor": doctor,
            "patient_count": len(patients_by_doctor.get(doctor.get("doctor_id"), set())),
        }
        for doctor in doctors
    ]
    return {"hospital_id": hospital_id, "doctors": rows, "count": len(rows)}


def list_hospital_relations(hospital_id: str) -> dict:
    relations = list_active_relations_for_hospital(hospital_id)
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
    if doctor.get("hospital_id") != hospital_id:
        raise HTTPException(status_code=403, detail="Doctor does not belong to this hospital")

    relation = create_relation(
        user_id=user_id,
        doctor_id=doctor_id,
        relation_type=relation_type,
        linked_by=linked_by,
        hospital_id=hospital_id,
    )
    return {"relation": relation}
