"""
RSA encryption for ABDM sensitive values.

Why the key is hardcoded here (not fetched from the certificate endpoint)
─────────────────────────────────────────────────────────────────────────
The ABDM public key is stable infrastructure — the same key has been in use
across sandbox and production for years. ABDM announces key rotations well
in advance. Fetching it dynamically at cold-start would:

  1. Add a network call before every first encryption in a new Lambda instance
  2. Create a hard dependency — if the cert endpoint is temporarily down,
     ALL encryption (and therefore all ABHA API calls) would fail
  3. Introduce DER vs PEM format ambiguity in the response

The hardcoded approach:
  - Zero latency — key is decoded once and cached in module-level memory
  - No external dependency for a value that almost never changes
  - Env var escape hatch: set ABDM_PUBLIC_KEY_B64 to override without redeploying

When ABDM rotates the key:
  Update ABDM_PUBLIC_KEY_B64 in template.yaml and redeploy ABHALambda.

Key format: DER-encoded RSA public key, Base64-encoded
Source: GET https://abhasbx.abdm.gov.in/abha/api/v3/profile/public/certificate
"""
import base64
import os

from cryptography.hazmat.primitives.serialization import load_der_public_key
from cryptography.hazmat.primitives.asymmetric.padding import OAEP, MGF1
from cryptography.hazmat.primitives.hashes import SHA1

from app.logger import get_logger

logger = get_logger(__name__)

# ABDM RSA public key — DER format, Base64-encoded
# Verified working key (sandbox + production as of 2025)
_ABDM_PUBLIC_KEY_B64 = (
    "MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAstWB95C5pHLXiYW59qyO"
    "4Xb+59KYVm9Hywbo77qETZVAyc6VIsxU+UWhd/k/YtjZibCznB+HaXWX9TVTFs9"
    "Nwgv7LRGq5uLczpZQDrU7dnGkl/urRA8p0Jv/f8T0MZdFWQgks91uFffeBmJOb5"
    "8u68ZRxSYGMPe4hb9XXKDVsgoSJaRNYviH7RgAI2QhTCwLEiMqIaUX3p1SAc178"
    "ZlN8qHXSSGXvhDR1GKM+y2DIyJqlzfik7lD14mDY/I4lcbftib8cv7llkybtjX1A"
    "ayfZp4XpmIXKWv8nRM488/jOAF81Bi13paKgpjQUUuwq9tb5Qd/DChytYgBTBTJF"
    "e7irDFCmTIcqPr8+IMB7tXA3YXPp3z605Z6cGoYxezUm2Nz2o6oUmarDUntDhq/P"
    "nkNergmSeSvS8gD9DHBuJkJWZweG3xOPXiKQAUBr92mdFhJGm6fitO5jsBxgpmul"
    "xpG0oKDy9lAOLWSqK92JMcbMNHn4wRikdI9HSiXrrI7fLhJYTbyU3I4v5ESdEsay"
    "HXuiwO/1C8y56egzKSw44GAtEpbAkTNEEfK5H5R0QnVBIXOvfeF4tzGvmkfOO6nN"
    "XU3o/WAdOyV3xSQ9dqLY5MEL4sJCGY1iJBIAQ452s8v0ynJG5Yq+8hNhsCVnklC"
    "zAlsIzQpnSVDUVEzv17grVAw078CAwEAAQ=="
)

# Module-level cache — loaded once per Lambda execution context
_public_key = None


def _get_public_key():
    """Load the ABDM RSA public key, caching it for the Lambda lifetime."""
    global _public_key
    if _public_key is None:
        # Env var override lets you rotate the key without a code change
        key_b64 = os.environ.get("ABDM_PUBLIC_KEY_B64", _ABDM_PUBLIC_KEY_B64)
        key_der = base64.b64decode(key_b64)
        _public_key = load_der_public_key(key_der)
        logger.debug("[Encryption] ABDM public key loaded (DER)")
    return _public_key


def encrypt_value(value: str) -> str:
    """
    Encrypt a plaintext string with the ABDM RSA public key.

    Algorithm : RSA/ECB/OAEPWithSHA-1AndMGF1Padding
    Hash      : SHA-1
    MGF       : MGF1 with SHA-1
    Output    : Base64-encoded ciphertext

    Used for: Aadhaar number, OTP, ABHA number — anything ABDM requires encrypted.
    """
    ciphertext = _get_public_key().encrypt(
        value.strip().encode("utf-8"),
        OAEP(
            mgf=MGF1(algorithm=SHA1()),
            algorithm=SHA1(),
            label=None,
        ),
    )
    return base64.b64encode(ciphertext).decode("utf-8")
