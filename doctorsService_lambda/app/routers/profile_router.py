from fastapi import APIRouter, HTTPException
from app.models.schemas import DoctorProfileUpdate
from app.services.document_service import upload_doc_to_s3
from app.utils.error_utils import handle_exception
from app.config import DOCTORS_TABLE, HOSPITALS_TABLE
from app.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/doctorsService", tags=["Doctor Profile"])


def _validate_and_fetch_hospital(hospital_id: str) -> tuple[str | None, str | None]:
    """Validate hospital exists and is active. Returns (hospital_id, hospital_name)."""
    if not hospital_id:
        return None, None
    try:
        resp = HOSPITALS_TABLE.get_item(Key={"hospital_id": hospital_id})
        item = resp.get("Item")
        if not item:
            raise HTTPException(status_code=400, detail=f"Hospital {hospital_id} not found")
        if not item.get("is_active", True):
            raise HTTPException(status_code=400, detail=f"Hospital {hospital_id} is not active")
        return hospital_id, item.get("name", "")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error validating hospital {hospital_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to validate hospital")


@router.post("/updateProfile")
def complete_doctor_profile(data: DoctorProfileUpdate):
    try:
        doctor = DOCTORS_TABLE.get_item(Key={"doctor_id": data.doctor_id})
        if "Item" not in doctor:
            raise HTTPException(status_code=404, detail="Doctor not found. Please sign up first.")

        update_expr_parts, expr_values = [], {}
        doc_updates = {}

        for field in ["degreeCertificate", "govtIdProof", "profilePhoto"]:
            file_obj = getattr(data, field)
            if file_obj:
                url = upload_doc_to_s3(data.doctor_id, field, file_obj.filename, file_obj.base64_content)
                doc_updates[field] = url

        # Validate and fetch hospital if hospital_id provided; support clearing when explicitly sent as null/empty
        hospital_id_val, hospital_name_val = None, None
        sent_fields = data.model_dump(exclude_unset=True)
        if "hospital_id" in sent_fields:
            if data.hospital_id:
                hospital_id_val, hospital_name_val = _validate_and_fetch_hospital(data.hospital_id)
            # else: explicitly clear hospital assignment

        field_map = {
            "description": data.description,
            "specialization": data.specialization,
            "experience": data.experience,
            "fees": data.fees,
            "timings": data.timings,
            "licenseNumber": data.licenseNumber,
            "registrationId": data.registrationId,
            "affiliation": data.affiliation,
            "hospital_id": hospital_id_val,
            "hospital_name": hospital_name_val,
        }

        is_first_time = not doctor["Item"].get("onboarded", False)
        hospital_cleared = "hospital_id" in sent_fields and not data.hospital_id

        if is_first_time:
            for key in field_map:
                if field_map[key] is None:
                    # Skip hospital fields when cleared - handled below
                    if hospital_cleared and key in ("hospital_id", "hospital_name"):
                        continue
                    update_expr_parts.append(f"{key} = :{key}_null")
                    expr_values[f":{key}_null"] = None

        for key, val in field_map.items():
            if val is not None:
                update_expr_parts.append(f"{key} = :{key}")
                expr_values[f":{key}"] = val

        # When hospital_id explicitly cleared, set both to null
        if hospital_cleared:
            update_expr_parts.append("hospital_id = :hospital_id_null")
            update_expr_parts.append("hospital_name = :hospital_name_null")
            expr_values[":hospital_id_null"] = None
            expr_values[":hospital_name_null"] = None

        for i, (k, v) in enumerate(doc_updates.items()):
            placeholder = f":doc{i}"
            update_expr_parts.append(f"{k} = {placeholder}")
            expr_values[placeholder] = v

        update_expr_parts.append("onboarded = :ob")
        expr_values[":ob"] = True

        update_expr = "SET " + ", ".join(update_expr_parts)
        DOCTORS_TABLE.update_item(
            Key={"doctor_id": data.doctor_id},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values,
        )

        return {"message": "Doctor profile updated successfully."}
    except Exception as e:
        handle_exception(e, "Profile update")
