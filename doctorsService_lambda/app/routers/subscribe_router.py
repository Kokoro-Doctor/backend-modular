from fastapi import APIRouter, HTTPException
from app.models.schemas import SubscribeRequest
from app.utils.error_utils import handle_exception
from app.config import DOCTORS_TABLE, USERS_TABLE

router = APIRouter(prefix="/doctorsService", tags=["Subscribe"])

@router.post("/subscribe")
def subscribe_doctor(data: SubscribeRequest):
    try:
        doctor = DOCTORS_TABLE.get_item(Key={"doctor_id": data.doctor_id}).get("Item")
        user = USERS_TABLE.get_item(Key={"user_id": data.user_id}).get("Item")

        if not doctor:
            raise HTTPException(status_code=404, detail="Doctor not found")
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        subscribers = doctor.get("subscribers", [])
        subscribed = user.get("subscribed_doctors", [])
        if data.user_id in subscribers:
            return {"message": "Already subscribed"}

        DOCTORS_TABLE.update_item(
            Key={"doctor_id": data.doctor_id},
            UpdateExpression="SET subscribers = list_append(if_not_exists(subscribers, :empty), :u)",
            ExpressionAttributeValues={":u": [data.user_id], ":empty": []},
        )
        USERS_TABLE.update_item(
            Key={"user_id": data.user_id},
            UpdateExpression="SET subscribed_doctors = list_append(if_not_exists(subscribed_doctors, :empty), :d)",
            ExpressionAttributeValues={":d": [data.doctor_id], ":empty": []},
        )

        return {"message": "Subscribed successfully"}
    except Exception as e:
        handle_exception(e, "Subscribe doctor")
