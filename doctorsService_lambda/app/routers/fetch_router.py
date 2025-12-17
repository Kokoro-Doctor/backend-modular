from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from app.services.document_service import generate_presigned_url
from app.services.doctor_service import get_doctor
from app.utils.error_utils import handle_exception
from app.config import DOCTORS_TABLE, S3_BUCKET
from boto3.dynamodb.conditions import Attr

router = APIRouter(prefix="/doctorsService", tags=["Fetch Doctors"])

@router.get("/doctors")
def fetch_doctors(category: Optional[str] = Query(None, description="Filter by category")):
    try:
        response = (
            DOCTORS_TABLE.scan(FilterExpression=Attr("category").eq(category))
            if category else DOCTORS_TABLE.scan()
        )
        doctors = response.get("Items", [])
        for doc in doctors:
            for field in ["profilePhoto", "degreeCertificate", "govtIdProof"]:
                if field in doc and doc[field]:
                    # Extract key from S3 URL (handles both old and new bucket names)
                    url = doc[field]
                    # Extract key by finding "s3.amazonaws.com/" and taking everything after it
                    if "s3.amazonaws.com/" in url:
                        key = url.split("s3.amazonaws.com/", 1)[1]
                    else:
                        # If it's not a full URL, assume it's already a key
                        key = url
                    doc[field] = generate_presigned_url(key)
        return {"doctors": doctors}
    except Exception as e:
        handle_exception(e, "Fetch doctors")

@router.get("/doctor/{doctor_id}")
def get_doctor_by_id(doctor_id: str):
    """Get a single doctor by doctor_id"""
    try:
        doctor = get_doctor(doctor_id)
        
        # Generate presigned URLs for S3 fields
        for field in ["profilePhoto", "degreeCertificate", "govtIdProof"]:
            if field in doctor and doctor[field]:
                url = doctor[field]
                # Extract key from S3 URL
                if "s3.amazonaws.com/" in url:
                    key = url.split("s3.amazonaws.com/", 1)[1]
                else:
                    key = url
                doctor[field] = generate_presigned_url(key)
        
        return {"doctor": doctor}
    except HTTPException:
        raise
    except Exception as e:
        handle_exception(e, "Get doctor by ID")
