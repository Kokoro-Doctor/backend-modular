"""
HIP-initiated linking endpoints (Milestone 2) — Kokoro → ABDM outbound calls.

All linking APIs are async: they return 202 immediately and ABDM posts the
result to our registered webhook URL. See routers/webhook_router.py for the
inbound callback handlers.

hospital_id is Kokoro's internal hospital UUID. It is used to look up the
hospital's ABDM config (hip_id, facility_id, etc.) from HospitalAbdmConfig.

Endpoints:
  POST  /abha/link/generate-token      — 4.3.1: request link token for a patient
  POST  /abha/link/care-context        — 4.3.3: link care contexts using stored link token
  PATCH /abha/bridge/url               — 3.2.4: admin — register Kokoro webhook URL with ABDM
  POST  /abha/bridge/register-facility — 3.2.5: admin — register hospital facility + HRP bridge
  GET   /abha/bridge/find-bridge       — 3.2.6: live ABDM — find bridge by service ID
  GET   /abha/bridge/services          — 3.2.7: live ABDM — find services under our bridge
  GET   /abha/bridge/hospitals         — admin — list all registered hospitals (from DB)
  GET   /abha/transactions             — admin — inspect ABDM async request/callback log
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.abdm.schemas import CareContextPatient
from app.services import (
    abha_accounts_service,
    abdm_transactions_service,
    hip_linking_service,
    hospital_abdm_service,
)
from app.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/abha", tags=["HIP Linking"])


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class GenerateLinkTokenRequest(BaseModel):
    hospital_id: str                     # Kokoro internal hospital UUID
    abha_address: Optional[str] = None
    abha_number: Optional[str] = None
    name: str
    gender: str                          # M / F / O
    year_of_birth: int


class LinkCareContextRequest(BaseModel):
    hospital_id: str                     # Kokoro internal hospital UUID
    abha_address: str
    abha_number: Optional[str] = None
    patient: List[CareContextPatient]


class UpdateBridgeUrlRequest(BaseModel):
    url: str


class RegisterFacilityRequest(BaseModel):
    hospital_id: str                     # Kokoro internal hospital UUID
    facility_id: str                     # HFR-issued ID e.g. "IN2810014366"
    facility_name: str
    hip_name: str                        # ≤15 chars, alphanumeric — becomes X-HIP-ID
    service_type: str = "HIP"
    active: bool = True


# ---------------------------------------------------------------------------
# 4.3.1 — Generate link token
# ---------------------------------------------------------------------------

@router.post("/link/generate-token")
def generate_link_token(body: GenerateLinkTokenRequest):
    """
    Ask ABDM to generate a link token for a patient's ABHA at a specific hospital.
    Returns 202 immediately — the actual link token arrives at our webhook
    (/api/v3/hip/token/on-generate-token) and is stored automatically under
    that hospital's hip_id.

    Either abha_address or abha_number is required.
    """
    if not body.abha_address and not body.abha_number:
        raise HTTPException(
            status_code=400,
            detail="Either abha_address or abha_number must be provided.",
        )
    try:
        request_id = hip_linking_service.generate_link_token(
            hospital_id=body.hospital_id,
            name=body.name,
            gender=body.gender,
            year_of_birth=body.year_of_birth,
            abha_address=body.abha_address,
            abha_number=body.abha_number,
        )
        return {
            "message":      "Link token generation request accepted. Token will be available shortly.",
            "request_id":   request_id,
            "hospital_id":  body.hospital_id,
            "abha_address": body.abha_address,
            "abha_number":  body.abha_number,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[HIPLinking] generate_link_token failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 4.3.3 — Link care context
# ---------------------------------------------------------------------------

@router.post("/link/care-context")
def link_care_context(body: LinkCareContextRequest):
    """
    Link one or more care contexts to a patient's ABHA address for a specific hospital.
    Looks up the stored link token from DB scoped to hospital's hip_id.
    Call generate-token first if the token is not yet available.
    Returns 202 immediately; confirmation arrives at /api/v3/link/on_carecontext.
    """
    # Get hospital config to resolve hip_id
    hospital = hospital_abdm_service.get_or_raise(body.hospital_id)
    hip_id   = hospital["hip_id"]

    # Resolve abha_number to look up the stored link token
    abha_number = body.abha_number
    if not abha_number:
        record = abha_accounts_service.get_by_abha_address(body.abha_address)
        if record:
            abha_number = record.get("abha_number")

    if not abha_number:
        raise HTTPException(
            status_code=404,
            detail="Could not find an ABHA record for the given abha_address. Provide abha_number directly.",
        )

    link_token = abha_accounts_service.get_valid_link_token(abha_number, hip_id)
    if not link_token:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No valid link token found for this patient at hospital '{body.hospital_id}'. "
                "Call POST /abha/link/generate-token first and wait for the token to be generated."
            ),
        )

    try:
        request_id = hip_linking_service.link_care_context(
            hospital_id=body.hospital_id,
            abha_address=body.abha_address,
            patient_records=body.patient,
            link_token=link_token,
            abha_number=abha_number,
        )
        return {
            "message":      "Care context linking request accepted.",
            "request_id":   request_id,
            "hospital_id":  body.hospital_id,
            "abha_address": body.abha_address,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[HIPLinking] link_care_context failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 3.2.4 — Update bridge URL (admin — call once per environment)
# ---------------------------------------------------------------------------

@router.patch("/bridge/url")
def update_bridge_url(body: UpdateBridgeUrlRequest):
    """
    Register Kokoro's deployed API base URL as the ABDM bridge callback URL.
    ABDM will POST all async callbacks to {url}/api/v3/hip/...
    One-time setup per environment (sbx / prod).
    """
    try:
        hip_linking_service.update_bridge_url(body.url)
        return {"message": f"Bridge URL updated to {body.url}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[HIPLinking] update_bridge_url failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 3.2.5 — Register facility + HRP bridge (admin — once per hospital onboarding)
# ---------------------------------------------------------------------------

@router.post("/bridge/register-facility")
def register_facility(body: RegisterFacilityRequest):
    """
    Register a hospital facility with ABDM and store the config in DB.
    hip_name becomes the hospital's permanent X-HIP-ID for all future calls.
    Must be ≤15 characters, alphanumeric, unique per bridge per facility.
    bridge_id is Kokoro's ABDM client ID, taken from config (ABDM_CLIENT_ID).
    """
    try:
        hip_linking_service.register_facility(
            hospital_id=body.hospital_id,
            facility_id=body.facility_id,
            facility_name=body.facility_name,
            hip_name=body.hip_name,
            service_type=body.service_type,
            active=body.active,
        )
        return {
            "message":     f"Facility {body.facility_id} registered successfully.",
            "hospital_id": body.hospital_id,
            "hip_id":      body.hip_name,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[HIPLinking] register_facility failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 3.2.6 — Find bridge by service ID (live ABDM query)
# ---------------------------------------------------------------------------

@router.get("/bridge/find-bridge")
def find_bridge_by_service_id(service_id: str):
    """
    Query ABDM live for the bridge registered against a given service (HIP/HIU) ID.
    Returns the raw ABDM response — not from DB.
    """
    try:
        result = hip_linking_service.find_bridge_by_service_id(service_id)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[HIPLinking] find_bridge_by_service_id failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 3.2.7 — Find services by bridge ID (live ABDM query)
# ---------------------------------------------------------------------------

@router.get("/bridge/services")
def find_services_by_bridge_id():
    """
    Query ABDM live for all services (HIP/HIU) registered under our bridge.
    Returns the raw ABDM response — not from DB.
    """
    try:
        result = hip_linking_service.find_services_by_bridge_id()
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[HIPLinking] find_services_by_bridge_id failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Admin — transactions log
# ---------------------------------------------------------------------------

@router.get("/transactions")
def list_transactions(
    hip_id: Optional[str] = None,
    status: Optional[str] = None,
    request_id: Optional[str] = None,
    limit: int = 50,
):
    """
    Inspect ABDM async request/callback transactions.

    Query params:
      request_id  — fetch a single transaction (full request + callback bodies)
      hip_id      — all transactions for one hospital (uses GSI, newest first)
      status      — filter by PENDING | COMPLETED | FAILED
      limit       — max rows (default 50)

    With no hip_id/request_id returns the most recent transactions (scan).
    """
    try:
        if request_id:
            txn = abdm_transactions_service.get_hydrated(request_id)
            if not txn:
                raise HTTPException(status_code=404, detail=f"No transaction for request_id {request_id}")
            return {"transaction": txn}

        if hip_id:
            items = abdm_transactions_service.list_by_hip(hip_id, status=status, limit=limit)
        else:
            items = abdm_transactions_service.list_recent(status=status, limit=limit)
        return {"transactions": items, "count": len(items)}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[HIPLinking] list_transactions failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Admin — list registered hospitals
# ---------------------------------------------------------------------------

@router.get("/bridge/hospitals")
def list_hospitals():
    """Return all hospitals registered with ABDM."""
    try:
        hospitals = hospital_abdm_service.list_all()
        return {"hospitals": hospitals, "count": len(hospitals)}
    except Exception as e:
        logger.exception("[HIPLinking] list_hospitals failed")
        raise HTTPException(status_code=500, detail=str(e))
