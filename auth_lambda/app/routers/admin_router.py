"""
Internal admin endpoints for account management.
"""
from fastapi import APIRouter, HTTPException, Header

from app import config
from app.logger import get_logger
from app.models import schemas
from app.services.account_service import delete_user_account, delete_doctor_account
from app.services.user_service import get_user_by_phone
from app.services.doctor_service import get_doctor_by_phone

logger = get_logger(__name__)

router = APIRouter(prefix="/auth/admin", tags=["admin"])


@router.post("/delete-account")
def delete_account(
    data: schemas.DeleteAccountRequest,
    x_admin_key: str = Header(..., alias="x-admin-key", description="Internal admin key for authorization")
):
    """
    Internal endpoint to delete a user or doctor account by phone number.
    Deletes all related data from all tables and S3.
    
    Requires x-admin-key HTTP header for authorization.
    """
    try:
        # Verify admin key from header
        if x_admin_key != config.ADMIN_KEY:
            logger.warning(f"Unauthorized delete account attempt with key: {x_admin_key[:10]}...")
            raise HTTPException(status_code=403, detail="Unauthorized: Invalid admin key")

        # Normalize phone number
        from app.utils.db_utils import normalize_phone_number
        normalized_phone = normalize_phone_number(data.phoneNumber)
        if not normalized_phone:
            raise HTTPException(status_code=400, detail="Invalid phone number format")

        # Check if user exists
        user = get_user_by_phone(normalized_phone)
        if user:
            user_id = user.get("user_id")
            email = user.get("email")
            logger.info(f"Deleting user account: {user_id} (phone: {normalized_phone})")
            
            deleted_items = delete_user_account(user_id, normalized_phone, email)
            
            return {
                "success": True,
                "account_type": "user",
                "user_id": user_id,
                "phone_number": normalized_phone,
                "deleted_items": deleted_items,
                "message": f"User account {user_id} and all related data deleted successfully"
            }

        # Check if doctor exists
        doctor = get_doctor_by_phone(normalized_phone)
        if doctor:
            doctor_id = doctor.get("doctor_id")
            email = doctor.get("email")
            logger.info(f"Deleting doctor account: {doctor_id} (phone: {normalized_phone})")
            
            deleted_items = delete_doctor_account(doctor_id, normalized_phone, email)
            
            return {
                "success": True,
                "account_type": "doctor",
                "doctor_id": doctor_id,
                "phone_number": normalized_phone,
                "deleted_items": deleted_items,
                "message": f"Doctor account {doctor_id} and all related data deleted successfully"
            }

        # Neither user nor doctor found
        raise HTTPException(
            status_code=404,
            detail=f"No account found with phone number: {normalized_phone}"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting account: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

