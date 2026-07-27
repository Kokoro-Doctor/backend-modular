"""
Fidelius-compatible ECDH + AES-GCM primitives for ABDM health-data exchange.

This is a pure-Python port of ABDM's reference implementation, Fidelius CLI
(https://github.com/mgrmtech/fidelius-cli), kept at backend/tools/fidelius-cli
for regenerating test vectors. It is validated byte-for-byte against that CLI
by tests/test_fidelius.py.

WHY NOT plain X25519
────────────────────
ABDM does NOT use RFC 7748 X25519, despite the "Curve25519" name in the
protocol's keyMaterial. Fidelius uses BouncyCastle's `ECDH` over the named
curve `curve25519` — i.e. curve25519 in SHORT-WEIERSTRASS form, with an
unclamped scalar and classic EC point multiplication. The two produce
different shared secrets and different wire encodings, and are not
interchangeable.

Sending an RFC 8410 X25519 SubjectPublicKeyInfo (OID 1.3.101.110) makes ABDM
reject the push with:
    "encoded key spec not recognized algorithm identifier 1.3.101.110"

The scheme (see also tools/fidelius-cli/abdm/Encryption and Decryption
Implementation Guidelines for FHIR data in ABDM.md)
────────────────────────────────────────────────────────────────────────
  1. Each party generates an EC key pair on curve25519 + a 32-byte nonce.
  2. shared = ECDH(our_private, their_public)   -> 32-byte X coordinate
  3. xor    = our_nonce XOR their_nonce
     salt   = xor[:20]     (NOT the full 32 bytes)
     iv     = xor[-12:]    (taken from the nonces, NOT from the HKDF)
  4. aes_key = HKDF-SHA256(shared, salt, info=None, length=32)
  5. AES-256-GCM with a 128-bit tag.

Because the IV is derived from the nonces rather than randomly, encryption is
deterministic: the same keys + nonces + plaintext yield identical ciphertext.
The test suite relies on this to compare against frozen CLI vectors.
"""
import base64
import os
import secrets
from typing import Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# ---------------------------------------------------------------------------
# BouncyCastle CustomNamedCurves "curve25519" — short-Weierstrass domain
# parameters, extracted from the CLI's own x509PublicKey DER.
# ---------------------------------------------------------------------------
_P = 2 ** 255 - 19
_A = 0x2AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA984914A144
_B = 0x7B425ED097B425ED097B425ED097B425ED097B425ED097B4260B5E9C7710C864
_N = 0x1000000000000000000000000000000014DEF9DEA2F79CD65812631A5CF5D3ED
_GX = 0x2AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAD245A
_GY = 0x20AE19A1B8A086B4E01EDD2C7748D14C923D4D7E6D7C61B229E9C5A27ECED3D9
_G = (_GX, _GY)

_FIELD_BYTES = 32
_NONCE_BYTES = 32
_GCM_TAG_BITS = 128


# ---------------------------------------------------------------------------
# Curve arithmetic (affine; see module note in the README about constant time)
# ---------------------------------------------------------------------------

def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _point_add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and (y1 + y2) % _P == 0:
        return None
    if p1 == p2:
        lam = (3 * x1 * x1 + _A) * _inv(2 * y1) % _P
    else:
        lam = (y2 - y1) * _inv(x2 - x1) % _P
    x3 = (lam * lam - x1 - x2) % _P
    return (x3, (lam * (x1 - x3) - y1) % _P)


def _point_mul(k: int, point):
    result = None
    addend = point
    while k:
        if k & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        k >>= 1
    return result


def _is_on_curve(point) -> bool:
    x, y = point
    return (y * y - (x * x * x + _A * x + _B)) % _P == 0


def _decode_point(raw: bytes):
    """Decode an EC point in SEC1 form, mirroring BC's ECCurve.decodePoint."""
    if not raw:
        raise ValueError("empty public key")
    prefix = raw[0]
    if prefix == 0x04:
        if len(raw) != 65:
            raise ValueError(f"uncompressed point must be 65 bytes, got {len(raw)}")
        point = (int.from_bytes(raw[1:33], "big"), int.from_bytes(raw[33:65], "big"))
    elif prefix in (0x02, 0x03):
        if len(raw) != 33:
            raise ValueError(f"compressed point must be 33 bytes, got {len(raw)}")
        x = int.from_bytes(raw[1:33], "big")
        alpha = (pow(x, 3, _P) + _A * x + _B) % _P
        y = pow(alpha, (_P + 1) // 4, _P)
        if y % 2 != prefix % 2:
            y = _P - y
        point = (x, y)
    else:
        raise ValueError(f"unrecognised EC point prefix {prefix:#04x}")

    if not _is_on_curve(point):
        raise ValueError("public key is not a valid point on curve25519")
    return point


def _b64d(value: str) -> bytes:
    return base64.b64decode(value)


def _b64e(value: bytes) -> str:
    return base64.b64encode(value).decode()


# ---------------------------------------------------------------------------
# Key material
# ---------------------------------------------------------------------------

def generate_key_material() -> dict:
    """
    Generate one ephemeral curve25519 key pair + nonce.

    Mirrors the CLI's `generate-key-material` (gkm) command, including its
    encodings:
      privateKey — Java BigInteger.toByteArray(): signed, big-endian, minimal
                   length (so 32 or 33 bytes; 33 when the high bit is set).
      publicKey  — uncompressed SEC1 point 0x04||X||Y, 65 bytes / 88 b64 chars.
    """
    # Rejection sampling keeps the scalar uniform over [1, n-1].
    while True:
        d = int.from_bytes(secrets.token_bytes(_FIELD_BYTES), "big")
        if 1 <= d < _N:
            break

    q = _point_mul(d, _G)
    public = b"\x04" + q[0].to_bytes(32, "big") + q[1].to_bytes(32, "big")
    private = d.to_bytes((d.bit_length() // 8) + 1, "big")  # signed two's complement

    return {
        "privateKey": _b64e(private),
        "publicKey": _b64e(public),
        "nonce": _b64e(os.urandom(_NONCE_BYTES)),
    }


def load_public_key(public_key_b64: str) -> bytes:
    """
    Normalise a counterparty's dhPublicKey.keyValue to a raw SEC1 point.

    ABDM peers send this in more than one encoding depending on their stack, so
    accept all of them (Fidelius itself switches on the base64 length):
      - 88 chars  / 65 bytes -> uncompressed point 0x04||X||Y
      - 44 chars  / 33 bytes -> compressed point
      - 412 chars            -> X.509 SPKI with explicit EC params; the trailing
                                65 bytes are the point
    """
    raw = _b64d(public_key_b64)
    if len(raw) in (33, 65):
        return raw
    if len(raw) > 65:
        return raw[-65:]
    raise ValueError(f"unrecognised public key encoding: {len(raw)} bytes")


def compute_shared_secret(private_key_b64: str, public_key_b64: str) -> bytes:
    """ECDH: the 32-byte X coordinate of private * peer_public."""
    d = int.from_bytes(_b64d(private_key_b64), "big", signed=True)
    peer = _decode_point(load_public_key(public_key_b64))
    shared = _point_mul(d, peer)
    if shared is None:
        raise ValueError("ECDH produced the point at infinity")
    return shared[0].to_bytes(_FIELD_BYTES, "big")


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------

def _xor_of_nonces(nonce_a: bytes, nonce_b: bytes) -> bytes:
    """Fidelius Utils.calculateXorOfBytes — cycles the second operand."""
    return bytes(nonce_a[i] ^ nonce_b[i % len(nonce_b)] for i in range(len(nonce_a)))


def derive_key_and_iv(
    private_key_b64: str,
    peer_public_key_b64: str,
    sender_nonce_b64: str,
    requester_nonce_b64: str,
) -> Tuple[bytes, bytes]:
    """
    Derive the shared (aes_key, iv).

    Note the argument order: the XOR is always sender_nonce XOR requester_nonce
    regardless of which side is calling, so both parties land on the same salt
    and IV.
    """
    xor = _xor_of_nonces(_b64d(sender_nonce_b64), _b64d(requester_nonce_b64))
    salt, iv = xor[:20], xor[-12:]
    shared = compute_shared_secret(private_key_b64, peer_public_key_b64)
    key = HKDF(algorithm=SHA256(), length=32, salt=salt, info=None).derive(shared)
    return key, iv


# ---------------------------------------------------------------------------
# Encrypt / decrypt
# ---------------------------------------------------------------------------

def encrypt(
    plaintext: str,
    sender_nonce_b64: str,
    requester_nonce_b64: str,
    sender_private_key_b64: str,
    requester_public_key_b64: str,
) -> str:
    """Encrypt `plaintext`, returning base64 ciphertext with the GCM tag appended."""
    key, iv = derive_key_and_iv(
        sender_private_key_b64, requester_public_key_b64,
        sender_nonce_b64, requester_nonce_b64,
    )
    return _b64e(AESGCM(key).encrypt(iv, plaintext.encode("utf-8"), None))


def decrypt(
    ciphertext_b64: str,
    requester_nonce_b64: str,
    sender_nonce_b64: str,
    requester_private_key_b64: str,
    sender_public_key_b64: str,
) -> str:
    """
    Decrypt base64 ciphertext produced by `encrypt` (or by Fidelius).

    Raises cryptography.exceptions.InvalidTag if the data or key material is
    wrong — unlike the Fidelius CLI, which swallows the exception and returns
    an empty string.
    """
    key, iv = derive_key_and_iv(
        requester_private_key_b64, sender_public_key_b64,
        sender_nonce_b64, requester_nonce_b64,
    )
    return AESGCM(key).decrypt(iv, _b64d(ciphertext_b64), None).decode("utf-8")
