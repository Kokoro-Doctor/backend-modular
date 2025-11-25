from app.config import DOCTORS_TABLE, USERS_TABLE, AVAILABILITY_TABLE
from fastapi import HTTPException

def get_doctor(doctor_id: str):
    doctor = DOCTORS_TABLE.get_item(Key={"doctor_id": doctor_id})
    if "Item" not in doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    return doctor["Item"]

def get_user(user_id: str):
    user = USERS_TABLE.get_item(Key={"user_id": user_id})
    if "Item" not in user:
        raise HTTPException(status_code=404, detail="User not found")
    return user["Item"]
