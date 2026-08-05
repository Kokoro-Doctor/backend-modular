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
    Internal endpoint to delete user, doctor, and/or hospital account data.

    Send phoneNumber to resolve user/doctor accounts and a hospital whose
    contact number matches. Send hospital_id for an exact hospital sweep,
    including a partially-deleted hospital whose profile row is already gone.
    Both fields may be supplied in one request.

    Requires x-admin-key HTTP header for authorization.
    """
    try:
        # Verify admin key from header
        if x_admin_key != config.ADMIN_KEY:
            logger.warning(f"Unauthorized delete account attempt with key: {x_admin_key[:10]}...")
            raise HTTPException(status_code=403, detail="Unauthorized: Invalid admin key")

        try:
            result = delete_account_by_phone(
                phone_number=data.phoneNumber,
                hospital_id=data.hospital_id,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        if not result["account_found"]:
            raise HTTPException(
                status_code=404,
                detail=(
                    "No account or related data found for target: "
                    f"phoneNumber={result['phone_number']!r}, "
                    f"hospital_id={data.hospital_id!r}"
                )
            )

        types = result["account_types"]
        if types:
            label = ", ".join(types)
            target = result["phone_number"] or result["hospital_id"]
            message = f"Deleted {label} account data for {target}"
        else:
            # Identity rows were gone but orphaned data remained and was cleaned up.
            target = result["phone_number"] or result["hospital_id"]
            message = f"No profile row found; cleaned up leftover data for {target}"

        success = not result["errors"]
        if not success:
            message = f"Partial deletion for {target}; retry after resolving reported errors"

        return {
            "success": success,
            "phone_number": result["phone_number"],
            "email": result["email"],
            "account_types": types,
            "user_id": result["user_id"],
            "doctor_id": result["doctor_id"],
            "hospital_id": result["hospital_id"],
            "hospital_ids": result["hospital_ids"],
            "deleted": result["deleted"],
            "errors": result["errors"],
            "message": message,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting account: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
