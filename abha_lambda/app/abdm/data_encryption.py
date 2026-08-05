"""
ABDM Data-Flow encryption (Section 6.3.5) — the ABDM protocol adapter.

This module is symmetric and serves BOTH directions:

  • HIP push  (Milestone 2): encrypt_care_context_bundles() — we hold the records,
    encrypt them against the HIU's public key, and push.
  • HIU receive (Milestone 3): decrypt_entries() — we requested records, the HIP
    pushed them encrypted against OUR public key; we decrypt with our stored
    ephemeral private key.

All cryptography lives in app/abdm/fidelius.py, which is a validated port of
ABDM's reference implementation (Fidelius CLI). This module only maps between
that primitive and ABDM's payload shapes — keyMaterial, entries, checksums.
Keep it that way: fidelius.py must stay free of ABDM protocol structures so it
can be diffed directly against the CLI in tests/test_fidelius.py.

SECURITY CAVEAT: the scheme derives a single (aes_key, iv) per session from the
XORed nonces, so that pair is reused across every entry in one push. This is
ABDM's design, not ours — it is safe only because the key pair and nonce are
freshly generated per session. Never reuse a key material across sessions.
"""
import base64
import hashlib
from datetime import datetime, timezone, timedelta
from typing import List, Tuple

from app.abdm import fidelius
from app.logger import get_logger

logger = get_logger(__name__)

CRYPTO_ALG = "ECDH"
CURVE = "Curve25519"
PARAMETERS = "Curve25519/32byte random key"


# ---------------------------------------------------------------------------
# Key material
# ---------------------------------------------------------------------------

def generate_key_material() -> Tuple[str, str, dict]:
    """
    Generate one ephemeral key pair + nonce for a single data session.

    Returns:
        (private_key_b64, nonce_b64, key_material) where key_material is the
        ABDM `keyMaterial` dict to hand to the counterparty. The caller MUST
        persist private_key_b64 + nonce_b64 if it needs to decrypt later
        (HIU flow); for the HIP encrypt flow they are used immediately.

    `keyValue` is the DER SubjectPublicKeyInfo (412 base64 chars), NOT the raw
    88-char EC point. ABDM's HIU feeds this field straight to Java's
    X509EncodedKeySpec, so the bare point is rejected with
    "failed to construct sequence from byte[] Extra data detected in stream".
    See the OID / encoding notes in fidelius.py — both wire formats have been
    rejected by the live sandbox for different reasons, and this is the one it
    accepts.
    """
    material = fidelius.generate_key_material()

    key_material = {
        "cryptoAlg": CRYPTO_ALG,
        "curve": CURVE,
        "dhPublicKey": {
            "expiry": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "parameters": PARAMETERS,
            "keyValue": material["x509PublicKey"],
        },
        "nonce": material["nonce"],
    }
    return material["privateKey"], material["nonce"], key_material


def _peer(key_material: dict) -> Tuple[str, str]:
    """Pull (public_key_b64, nonce_b64) out of a counterparty's keyMaterial."""
    try:
        public_key = key_material["dhPublicKey"]["keyValue"]
        nonce = key_material["nonce"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"malformed ABDM keyMaterial: {exc}") from exc
    if not public_key or not nonce:
        raise ValueError("ABDM keyMaterial is missing dhPublicKey.keyValue or nonce")
    return public_key, nonce


# ---------------------------------------------------------------------------
# HIP push (Milestone 2 — encrypt)
# ---------------------------------------------------------------------------

def encrypt_care_context_bundles(
    bundles: List[Tuple[str, str]],
    hiu_key_material: dict,
) -> Tuple[List[dict], dict]:
    """
    Encrypt FHIR bundles for one HIU push session (6.3.5).

    Args:
        bundles: list of (care_context_reference, fhir_json_string).
        hiu_key_material: the HIU's `keyMaterial` (public key + nonce) from 6.3.3.

    Returns:
        (entries, sender_key_material) — entries ready for the data push and the
        HIP's own keyMaterial so the HIU can derive the same secret.
    """
    hiu_public_key, hiu_nonce = _peer(hiu_key_material)
    private_key_b64, nonce_b64, sender_key_material = generate_key_material()

    entries = []
    for ref, fhir_json in bundles:
        entries.append({
            "content": fidelius.encrypt(
                fhir_json,
                sender_nonce_b64=nonce_b64,
                requester_nonce_b64=hiu_nonce,
                sender_private_key_b64=private_key_b64,
                requester_public_key_b64=hiu_public_key,
            ),
            "media": "application/fhir+json",
            # ABDM does not pin a digest for entries[].checksum; keeping the
            # SHA-256 this module has always sent. If a HIU ever rejects it,
            # MD5 hex is the other encoding seen in the wild.
            "checksum": hashlib.sha256(fhir_json.encode("utf-8")).hexdigest(),
            "careContextReference": ref,
        })

    logger.info("[DataEncryption] Encrypted %d bundle(s) for push", len(entries))
    return entries, sender_key_material


# ---------------------------------------------------------------------------
# HIU receive (Milestone 3 — decrypt)
# ---------------------------------------------------------------------------

def decrypt_entries(
    entries: List[dict],
    hip_key_material: dict,
    receiver_private_key_b64: str,
    receiver_nonce_b64: str,
) -> List[dict]:
    """
    Decrypt the entries a HIP pushed to our HIU dataPushUrl (6.3.5 inbound).

    Args:
        entries: the `entries` list from the push body.
        hip_key_material: the HIP's `keyMaterial` from the same push body.
        receiver_private_key_b64 / receiver_nonce_b64: OUR ephemeral key + nonce,
            persisted when we sent the health-information request.

    Returns:
        list of {careContextReference, fhir (plaintext JSON), checksum_ok}.
    """
    hip_public_key, hip_nonce = _peer(hip_key_material)

    decrypted = []
    for entry in entries:
        plaintext = fidelius.decrypt(
            entry["content"],
            requester_nonce_b64=receiver_nonce_b64,
            sender_nonce_b64=hip_nonce,
            requester_private_key_b64=receiver_private_key_b64,
            sender_public_key_b64=hip_public_key,
        )
        checksum = entry.get("checksum")
        checksum_ok = _checksum_matches(plaintext, checksum)
        if not checksum_ok:
            logger.warning(
                "[DataEncryption] checksum mismatch for careContext=%s",
                entry.get("careContextReference"),
            )
        decrypted.append({
            "careContextReference": entry.get("careContextReference"),
            "fhir": plaintext,
            "checksum_ok": checksum_ok,
        })

    logger.info("[DataEncryption] Decrypted %d entrie(s)", len(decrypted))
    return decrypted


def _checksum_matches(plaintext: str, checksum) -> bool:
    """
    Verify a HIP's entry checksum.

    ABDM does not pin the digest, and implementations differ (the spec's own
    samples use MD5). Accept the common ones rather than flagging a spurious
    mismatch. A missing checksum is reported as not-verified, never as a match.
    """
    if not checksum:
        return False
    raw = plaintext.encode("utf-8")
    candidates = {
        hashlib.md5(raw).hexdigest(),
        hashlib.sha256(raw).hexdigest(),
        base64.b64encode(hashlib.md5(raw).digest()).decode(),
    }
    return str(checksum).strip().lower() in {c.lower() for c in candidates}
