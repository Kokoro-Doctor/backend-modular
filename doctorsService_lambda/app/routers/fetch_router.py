from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
from app.services.document_service import generate_presigned_url
from app.services.doctor_service import get_doctor
from app.utils.error_utils import handle_exception
from app.config import DOCTORS_TABLE, S3_BUCKET
from boto3.dynamodb.conditions import Attr

router = APIRouter(prefix="/doctorsService", tags=["Fetch Doctors"])


def _extract_s3_key(url: str) -> str:
    """Extract S3 key from URL or return the key if already a key"""
    if "s3.amazonaws.com/" in url:
        return url.split("s3.amazonaws.com/", 1)[1]
    return url


def _generate_presigned_urls_for_doctor(doc: dict) -> dict:
    """Generate presigned URLs for all S3 fields in a doctor record"""
    for field in ["profilePhoto", "degreeCertificate", "govtIdProof"]:
        if field in doc and doc[field]:
            try:
                key = _extract_s3_key(doc[field])
                doc[field] = generate_presigned_url(key)
            except Exception as e:
                # Log error but continue processing other fields
                # Keep original value if presigned URL generation fails
                pass
    return doc


@router.get("/doctors")
def fetch_doctors(
    category: Optional[str] = Query(None, description="Filter by category"),
    hospital_id: Optional[str] = Query(None, description="Filter by hospital_id"),
):
    try:
        filter_expr = None
        if category:
            filter_expr = Attr("category").eq(category)
        if hospital_id:
            hospital_attr = Attr("hospital_id").eq(hospital_id)
            filter_expr = hospital_attr if filter_expr is None else filter_expr & hospital_attr

        response = (
            DOCTORS_TABLE.scan(FilterExpression=filter_expr)
            if filter_expr else DOCTORS_TABLE.scan()
        )
        doctors = response.get("Items", [])
        
        # Parallelize presigned URL generation for all doctors
        # Using ThreadPoolExecutor to parallelize S3 API calls
        with ThreadPoolExecutor(max_workers=10) as executor:
            doctors = list(executor.map(_generate_presigned_urls_for_doctor, doctors))
        
        return {"doctors": doctors}
    except Exception as e:
        handle_exception(e, "Fetch doctors")

@router.get("/doctor/{doctor_id}")
def get_doctor_by_id(doctor_id: str):
    """Get a single doctor by doctor_id"""
    try:
        doctor = get_doctor(doctor_id)
        
        # Generate presigned URLs for S3 fields using the same helper function
        doctor = _generate_presigned_urls_for_doctor(doctor)
        
        return {"doctor": doctor}
    except HTTPException:
        raise
    except Exception as e:
        handle_exception(e, "Get doctor by ID")
