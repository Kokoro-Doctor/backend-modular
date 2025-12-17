"""
Doctor service - handles doctor-related database operations.
"""
from fastapi import HTTPException
from app.config import DOCTORS_TABLE, USERS_TABLE


def get_doctor(doctor_id: str):
    """Get doctor by doctor_id"""
    doctor = DOCTORS_TABLE.get_item(Key={"doctor_id": doctor_id})
    if "Item" not in doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    return doctor["Item"]


def get_user(user_id: str):
    """Get user by user_id"""
    user = USERS_TABLE.get_item(Key={"user_id": user_id})
    if "Item" not in user:
        raise HTTPException(status_code=404, detail="User not found")
    return user["Item"]

