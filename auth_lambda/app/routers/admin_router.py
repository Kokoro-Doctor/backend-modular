"""
Internal admin endpoints for account management.
"""
from fastapi import APIRouter, HTTPException, Header

from app import config
from app.logger import get_logger
from app.models import schemas
from app.services.account_service import delete_account_by_phone

logger = get_logger(__name__)

router = APIRouter(prefix="/auth/admin", tags=["admin"])


@router.post("/delete-account")
def delete_account(
    data: schemas.DeleteAccountRequest,
    x_admin_key: str = Header(..., alias="x-admin-key", description="Internal admin key for authorization")
):
    """
    Internal endpoint to delete a user and/or doctor account by phone number.

    Deletes ALL related data across every table and S3 prefix. The account is
    resolved from any surviving trace (the Users/Doctors profile row OR the
    AuthTable identity record), so it also cleans up partially-deleted accounts
    whose profile row was already removed by hand. A 404 is returned only when
    no account and no leftover data exist for the phone number.

    Requires x-admin-key HTTP header for authorization.
    """
    try:
        # Verify admin key from header
        if x_admin_key != config.ADMIN_KEY:
            logger.warning(f"Unauthorized delete account attempt with key: {x_admin_key[:10]}...")
            raise HTTPException(status_code=403, detail="Unauthorized: Invalid admin key")

        try:
            result = delete_account_by_phone(data.phoneNumber)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        if not result["account_found"]:
            raise HTTPException(
                status_code=404,
                detail=f"No account or related data found for phone number: {result['phone_number']}"
            )

        types = result["account_types"]
        if types:
            label = " and ".join(types)
            message = f"Deleted {label} account and all related data for {result['phone_number']}"
        else:
            # Identity rows were gone but orphaned data remained and was cleaned up.
            message = f"No profile row found; cleaned up leftover data for {result['phone_number']}"

        return {
            "success": True,
            "phone_number": result["phone_number"],
            "email": result["email"],
            "account_types": types,
            "user_id": result["user_id"],
            "doctor_id": result["doctor_id"],
            "deleted": result["deleted"],
            "errors": result["errors"],
            "message": message,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting account: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

