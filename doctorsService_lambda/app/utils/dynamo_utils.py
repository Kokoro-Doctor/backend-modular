from app.config import DOCTORS_TABLE, USERS_TABLE, AVAILABILITY_TABLE
from fastapi import HTTPException

def get_doctor(email: str):
    doctor = DOCTORS_TABLE.get_item(Key={"email": email})
    if "Item" not in doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    return doctor["Item"]

def get_user(email: str):
    user = USERS_TABLE.get_item(Key={"email": email})
    if "Item" not in user:
        raise HTTPException(status_code=404, detail="User not found")
    return user["Item"]
