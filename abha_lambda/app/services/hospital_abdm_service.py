"""
HospitalAbdmConfig DynamoDB service.

Table: HospitalAbdmConfig
PK:    hospital_id   (Kokoro's internal hospital/clinic UUID)
GSI:   hip_id-index  (lookup by ABDM HIP service ID — used in webhook routing)

One record per onboarded hospital. Written when a hospital is registered with
ABDM via 3.2.5. Read on every HIP API call to get the correct X-HIP-ID and
X-HIU-ID for that hospital.

Fields
──────
hospital_id      Kokoro internal ID (PK)
facility_id      HFR-issued facility ID e.g. "IN2810014366"
facility_name    Human-readable facility name
bridge_id        Kokoro ABDM bridge ID e.g. "SBX_KOKORO"
hip_id           ABDM HIP service ID (= hip_name used during 3.2.5 registration)
hiu_id           ABDM HIU service ID (set when HIU is also registered)
hip_name         ≤15 char alphanumeric label used during ABDM registration
abdm_status      "registered" | "pending" | "inactive"
created_at       ISO timestamp (set once on first write)
updated_at       ISO timestamp (updated on every write)
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

from app import config
from app.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def save(
    hospital_id: str,
    facility_id: str,
    facility_name: str,
    bridge_id: str,
    hip_name: str,
    hip_id: Optional[str] = None,
    hiu_id: Optional[str] = None,
    abdm_status: str = "registered",
) -> None:
    """
    Upsert the ABDM config for a hospital after successful 3.2.5 registration.
    hip_id defaults to hip_name — ABDM uses the hip_name value as the serviceId.
    """
    now = datetime.now(timezone.utc).isoformat()
    fields = {
        "facility_id":    facility_id,
        "facility_name":  facility_name,
        "bridge_id":      bridge_id,
        "hip_name":       hip_name,
        "hip_id":         hip_id or hip_name,   # serviceId == hipName in ABDM
        "abdm_status":    abdm_status,
        "updated_at":     now,
    }
    if hiu_id:
        fields["hiu_id"] = hiu_id

    set_parts = [f"#{k} = :{k}" for k in fields]
    names     = {f"#{k}": k for k in fields}
    values    = {f":{k}": v for k, v in fields.items()}

    # Set created_at only on first write
    set_parts.append("#created_at = if_not_exists(#created_at, :created_at)")
    names["#created_at"]   = "created_at"
    values[":created_at"]  = now

    config.hospital_abdm_table.update_item(
        Key={"hospital_id": hospital_id},
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )
    logger.info(
        "[HospitalAbdmService] Saved hospital_id=%s hip_id=%s facility_id=%s",
        hospital_id, hip_id or hip_name, facility_id,
    )


def update_status(hospital_id: str, abdm_status: str) -> None:
    """Update only the abdm_status field."""
    now = datetime.now(timezone.utc).isoformat()
    config.hospital_abdm_table.update_item(
        Key={"hospital_id": hospital_id},
        UpdateExpression="SET #s = :s, #u = :u",
        ExpressionAttributeNames={"#s": "abdm_status", "#u": "updated_at"},
        ExpressionAttributeValues={":s": abdm_status, ":u": now},
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get(hospital_id: str) -> Optional[dict]:
    """Return the raw record for hospital_id, or None if not found."""
    resp = config.hospital_abdm_table.get_item(Key={"hospital_id": hospital_id})
    return resp.get("Item")


def get_or_raise(hospital_id: str) -> dict:
    """
    Return the ABDM config record for hospital_id.
    Raises 404 if the hospital has not been registered with ABDM yet.
    """
    record = get(hospital_id)
    if not record:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Hospital '{hospital_id}' has not been registered with ABDM. "
                "Call POST /abha/bridge/register-facility first."
            ),
        )
    if record.get("abdm_status") == "inactive":
        raise HTTPException(
            status_code=403,
            detail=f"Hospital '{hospital_id}' ABDM registration is inactive.",
        )
    return record


def get_by_hip_id(hip_id: str) -> Optional[dict]:
    """
    Query the hip_id-index GSI to find the hospital record by ABDM HIP service ID.
    Used in webhook routing — ABDM sends X-HIP-ID on every callback.
    Requires a GSI named 'hip_id-index' on the HospitalAbdmConfig table.
    """
    resp = config.hospital_abdm_table.query(
        IndexName="hip_id-index",
        KeyConditionExpression="hip_id = :hid",
        ExpressionAttributeValues={":hid": hip_id},
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def get_by_hiu_id(hiu_id: str) -> Optional[dict]:
    """
    Find the hospital record by ABDM HIU service ID (M3 callback routing — ABDM
    sends X-HIU-ID). Tries the hiu_id-index GSI first; falls back to hip_id-index
    because in the sandbox a single serviceId usually acts as both HIP and HIU.
    """
    try:
        resp = config.hospital_abdm_table.query(
            IndexName="hiu_id-index",
            KeyConditionExpression="hiu_id = :hid",
            ExpressionAttributeValues={":hid": hiu_id},
            Limit=1,
        )
        items = resp.get("Items", [])
        if items:
            return items[0]
    except Exception:
        # GSI may not exist on older stacks — fall through to hip_id lookup
        logger.debug("[HospitalAbdmService] hiu_id-index query failed; falling back to hip_id")
    return get_by_hip_id(hiu_id)


def resolve_hiu_id(record: dict) -> str:
    """
    Return the ABDM HIU service ID for a hospital config record.
    Falls back to hip_id when hiu_id is unset (sandbox services act as both).
    """
    return record.get("hiu_id") or record["hip_id"]


def list_all() -> list:
    """Return all hospital ABDM config records (for admin use)."""
    resp = config.hospital_abdm_table.scan()
    return resp.get("Items", [])
