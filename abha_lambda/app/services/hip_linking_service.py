"""
HIP-initiated linking business logic (Milestone 2).

APIs covered:
  3.2.4  update_bridge_url    — register Kokoro's webhook URL with ABDM (one-time admin)
  3.2.5  register_facility    — link facility + HRP bridge, save config to HospitalAbdmConfig
  4.3.1  generate_link_token  — HIP requests a link token for a patient's ABHA
  4.3.3  link_care_context    — HIP links health records using the stored link token

Every HIP API call requires hospital_id so the correct X-HIP-ID is resolved
from HospitalAbdmConfig and passed to the ABDM gateway.

Async callback handlers live in routers/webhook_router.py, not here.
"""
from typing import List, Optional

from app.abdm import hip_client
from app.abdm.schemas import CareContextPatient
from app.services import hospital_abdm_service
from app.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 3.2.4  Update bridge URL (one-time admin — register our webhook base URL)
# ---------------------------------------------------------------------------

def update_bridge_url(url: str) -> None:
    """
    Register Kokoro's deployed base URL with ABDM as the HIP bridge callback URL.
    ABDM will POST all async callbacks to {url}/api/v3/hip/...
    Call this once per environment after deploying the Lambda.
    No X-HIP-ID needed for this call per ABDM spec.
    """
    logger.info("[HIPLinkingService] Updating bridge URL to %s", url)
    hip_client.patch("/api/hiecm/gateway/v3/bridge/url", {"url": url})
    logger.info("[HIPLinkingService] Bridge URL updated")


# ---------------------------------------------------------------------------
# 3.2.5  Facility + software (HRP) registration
# ---------------------------------------------------------------------------

def register_facility(
    hospital_id: str,
    facility_id: str,
    facility_name: str,
    bridge_id: str,
    hip_name: str,
    service_type: str = "HIP",
    active: bool = True,
) -> None:
    """
    Register a hospital facility with ABDM and persist the config in
    HospitalAbdmConfig table.

    hip_name becomes the ABDM serviceId (X-HIP-ID) for this hospital.
    Must be ≤15 characters, alphanumeric, unique per bridge per facility.

    We use hip_name as a temporary hip_id to call the facility registration
    endpoint (it's a one-time setup, not a patient-specific call).
    """
    logger.info(
        "[HIPLinkingService] Registering facility hospital_id=%s facilityId=%s bridgeId=%s",
        hospital_id, facility_id, bridge_id,
    )
    payload = {
        "facilityId":   facility_id,
        "facilityName": facility_name,
        "bridgeId":     bridge_id,
        "hipName":      hip_name,
        "type":         service_type,
        "active":       active,
    }
    hip_client.post_facility(
        "/v4/int/v1/bridges/MutipleHRPAddUpdateServices",
        payload,
        hip_id=hip_name,   # hipName == serviceId in ABDM
    )
    logger.info("[HIPLinkingService] Facility registered with ABDM, saving to DB")

    # Persist so all subsequent HIP API calls can look up hip_id by hospital_id
    hospital_abdm_service.save(
        hospital_id=hospital_id,
        facility_id=facility_id,
        facility_name=facility_name,
        bridge_id=bridge_id,
        hip_name=hip_name,
        hip_id=hip_name,       # ABDM uses hipName as the serviceId
        abdm_status="registered",
    )
    logger.info("[HIPLinkingService] hospital_id=%s saved to HospitalAbdmConfig", hospital_id)


# ---------------------------------------------------------------------------
# 4.3.1  Generate link token (HIP → ABDM, async — token arrives via callback)
# ---------------------------------------------------------------------------

def generate_link_token(
    hospital_id: str,
    name: str,
    gender: str,
    year_of_birth: int,
    abha_address: Optional[str] = None,
    abha_number: Optional[str] = None,
) -> None:
    """
    Ask ABDM to generate a link token for the patient identified by abhaAddress
    or abhaNumber. Returns immediately (202). The actual token arrives via the
    4.3.2 callback at /api/v3/hip/token/on-generate-token where it is stored
    in AbhaAccounts scoped to this hospital's hip_id.

    Either abha_address or abha_number must be provided.
    """
    if not abha_address and not abha_number:
        raise ValueError("Either abha_address or abha_number must be provided")

    hospital = hospital_abdm_service.get_or_raise(hospital_id)
    hip_id   = hospital["hip_id"]

    logger.info(
        "[HIPLinkingService] Generating link token hospital_id=%s hip_id=%s abha_address=%s",
        hospital_id, hip_id, abha_address,
    )
    payload = {
        "name":        name,
        "gender":      gender,
        "yearOfBirth": year_of_birth,
    }
    if abha_address:
        payload["abhaAddress"] = abha_address
    if abha_number:
        payload["abhaNumber"] = abha_number

    hip_client.post("/api/hiecm/v3/token/generate-token", payload, hip_id=hip_id)
    logger.info("[HIPLinkingService] Link token generation request sent (202 accepted)")


# ---------------------------------------------------------------------------
# 4.3.3  Link care context (HIP → ABDM, async — confirmation arrives via callback)
# ---------------------------------------------------------------------------

def link_care_context(
    hospital_id: str,
    abha_address: str,
    patient_records: List[CareContextPatient],
    link_token: str,
    abha_number: Optional[str] = None,
) -> None:
    """
    Link one or more care contexts against the patient's ABHA address.
    Requires a valid link token (previously obtained via 4.3.1 → 4.3.2 callback).
    Returns immediately (202). Confirmation arrives via 4.3.4 callback at
    /api/v3/link/on_carecontext.

    link_token is passed as X-LINK-TOKEN header by hip_client.post().
    """
    hospital = hospital_abdm_service.get_or_raise(hospital_id)
    hip_id   = hospital["hip_id"]

    logger.info(
        "[HIPLinkingService] Linking care context hospital_id=%s hip_id=%s abha_address=%s",
        hospital_id, hip_id, abha_address,
    )
    payload: dict = {
        "abhaAddress": abha_address,
        "patient":     [p.model_dump() for p in patient_records],
    }
    if abha_number:
        payload["abhaNumber"] = abha_number

    hip_client.post(
        "/api/hiecm/hip/v3/link/carecontext",
        payload,
        hip_id=hip_id,
        link_token=link_token,
    )
    logger.info("[HIPLinkingService] Care context link request sent (202 accepted)")
