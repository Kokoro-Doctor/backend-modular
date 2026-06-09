"""
ABDM Data-Flow encryption (Section 6.3.5) — real ECDH (Curve25519) + AES-GCM.

This module is symmetric and serves BOTH directions:

  • HIP push  (Milestone 2): encrypt_care_context_bundles() — we hold the records,
    encrypt them against the HIU's public key, and push.
  • HIU receive (Milestone 3): decrypt_entries() — we requested records, the HIP
    pushed them encrypted against OUR public key; we decrypt with our stored
    ephemeral private key.

Scheme (per the ABDM Data-Sharing spec / Kokoro WASA audit §C-3)
────────────────────────────────────────────────────────────────
  1. Each party generates an ephemeral X25519 key pair + a 32-byte nonce.
  2. shared = ECDH(our_private, their_public)
  3. salt   = our_nonce XOR their_nonce
  4. okm    = HKDF-SHA256(shared, salt, info=b"", length=44)
             aes_key = okm[:32]   (AES-256)
             iv      = okm[32:44] (96-bit GCM nonce)
  5. AES-256-GCM encrypt/decrypt each FHIR bundle.
  6. checksum = SHA-256(plaintext) hex.

Because both sides derive the same (aes_key, iv) from the XORed nonces, the
receiver reproduces the key without any extra exchange.

SECURITY CAVEAT: a single (aes_key, iv) is reused across all entries in one
push session — this mirrors ABDM's reference scheme for sandbox interop. It is
safe only because the ephemeral key+nonce are fresh per session. Prefer one
care-context per push, or move to per-entry IVs if/when ABDM supports it.
"""
import base64
import hashlib
import os
from datetime import datetime, timezone, timedelta
from typing import List, Tuple

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    PrivateFormat,
    NoEncryption,
)

from app.logger import get_logger

logger = get_logger(__name__)

CRYPTO_ALG = "ECDH"
CURVE = "Curve25519"
PARAMETERS = "Curve25519/32byte random key"
_HKDF_LEN = 44  # 32-byte AES key + 12-byte GCM IV


# ---------------------------------------------------------------------------
# Key material
# ---------------------------------------------------------------------------

def generate_key_material() -> Tuple[str, str, dict]:
    """
    Generate one ephemeral X25519 key pair + nonce for a single data session.

    Returns:
        (private_key_b64, nonce_b64, key_material) where key_material is the
        ABDM `keyMaterial` dict to hand to the counterparty. The caller MUST
        persist private_key_b64 + nonce_b64 if it needs to decrypt later
        (HIU flow); for the HIP encrypt flow they are used immediately.
    """
    private_key = X25519PrivateKey.generate()
    private_raw = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public_raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    nonce = os.urandom(32)

    key_material = {
        "cryptoAlg": CRYPTO_ALG,
        "curve": CURVE,
        "dhPublicKey": {
            "expiry": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "parameters": PARAMETERS,
            "keyValue": base64.b64encode(public_raw).decode(),
        },
        "nonce": base64.b64encode(nonce).decode(),
    }
    return (
        base64.b64encode(private_raw).decode(),
        base64.b64encode(nonce).decode(),
        key_material,
    )


def _derive_key_iv(
    private_key_b64: str,
    peer_public_key_b64: str,
    our_nonce_b64: str,
    peer_nonce_b64: str,
) -> Tuple[bytes, bytes]:
    """Reproduce the shared (aes_key, iv) from our private key + the peer's public key."""
    private_key = X25519PrivateKey.from_private_bytes(base64.b64decode(private_key_b64))
    peer_public = X25519PublicKey.from_public_bytes(base64.b64decode(peer_public_key_b64))
    shared = private_key.exchange(peer_public)

    our_nonce = base64.b64decode(our_nonce_b64)
    peer_nonce = base64.b64decode(peer_nonce_b64)
    salt = bytes(a ^ b for a, b in zip(our_nonce, peer_nonce))

    okm = HKDF(algorithm=SHA256(), length=_HKDF_LEN, salt=salt, info=b"").derive(shared)
    return okm[:32], okm[32:_HKDF_LEN]


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
    private_key_b64, nonce_b64, sender_key_material = generate_key_material()
    aes_key, iv = _derive_key_iv(
        private_key_b64,
        hiu_key_material["dhPublicKey"]["keyValue"],
        nonce_b64,
        hiu_key_material["nonce"],
    )
    aes = AESGCM(aes_key)

    entries = []
    for ref, fhir_json in bundles:
        plaintext = fhir_json.encode("utf-8")
        ciphertext = aes.encrypt(iv, plaintext, None)
        entries.append({
            "content": base64.b64encode(ciphertext).decode(),
            "media": "application/fhir+json",
            "checksum": hashlib.sha256(plaintext).hexdigest(),
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
    aes_key, iv = _derive_key_iv(
        receiver_private_key_b64,
        hip_key_material["dhPublicKey"]["keyValue"],
        receiver_nonce_b64,
        hip_key_material["nonce"],
    )
    aes = AESGCM(aes_key)

    decrypted = []
    for entry in entries:
        ciphertext = base64.b64decode(entry["content"])
        plaintext = aes.decrypt(iv, ciphertext, None)
        checksum_ok = hashlib.sha256(plaintext).hexdigest() == entry.get("checksum")
        if not checksum_ok:
            logger.warning(
                "[DataEncryption] checksum mismatch for careContext=%s",
                entry.get("careContextReference"),
            )
        decrypted.append({
            "careContextReference": entry.get("careContextReference"),
            "fhir": plaintext.decode("utf-8"),
            "checksum_ok": checksum_ok,
        })
    logger.info("[DataEncryption] Decrypted %d entrie(s)", len(decrypted))
    return decrypted
