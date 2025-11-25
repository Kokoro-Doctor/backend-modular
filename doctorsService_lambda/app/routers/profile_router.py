from fastapi import APIRouter, HTTPException
from app.models.schemas import DoctorProfileUpdate
from app.utils.s3_utils import upload_doc_to_s3
from app.utils.error_utils import handle_exception
from app.config import DOCTORS_TABLE
from app.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/doctorsService", tags=["Doctor Profile"])

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

        field_map = {
            "description": data.description,
            "specialization": data.specialization,
            "experience": data.experience,
            "fees": data.fees,
            "timings": data.timings,
            "licenseNumber": data.licenseNumber,
            "registrationId": data.registrationId,
            "affiliation": data.affiliation,
        }

        is_first_time = not doctor["Item"].get("onboarded", False)
        if is_first_time:
            for key in field_map:
                if field_map[key] is None:
                    update_expr_parts.append(f"{key} = :{key}_null")
                    expr_values[f":{key}_null"] = None

        for key, val in field_map.items():
            if val is not None:
                update_expr_parts.append(f"{key} = :{key}")
                expr_values[f":{key}"] = val

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
