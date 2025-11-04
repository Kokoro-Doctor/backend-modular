from fastapi import APIRouter, HTTPException
from app.models.schemas import FetchDoctorsRequest
from app.utils.s3_utils import generate_presigned_url
from app.utils.error_utils import handle_exception
from app.config import DOCTORS_TABLE, S3_BUCKET
from boto3.dynamodb.conditions import Attr

router = APIRouter(prefix="/doctorsService", tags=["Fetch Doctors"])

@router.post("/fetchDoctors")
def fetch_doctors(request: FetchDoctorsRequest):
    try:
        response = (
            DOCTORS_TABLE.scan(FilterExpression=Attr("category").eq(request.category))
            if request.category else DOCTORS_TABLE.scan()
        )
        doctors = response.get("Items", [])
        for doc in doctors:
            for field in ["profilePhoto", "degreeCertificate", "govtIdProof"]:
                if field in doc and doc[field]:
                    key = doc[field].replace(f"https://{S3_BUCKET}.s3.amazonaws.com/", "")
                    doc[field] = generate_presigned_url(key)
        return {"doctors": doctors}
    except Exception as e:
        handle_exception(e, "Fetch doctors")
