#!/usr/bin/env python3

import base64
from cryptography.hazmat.primitives.serialization import load_der_public_key
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes

# ABDM Public Key (from GET /abha/api/v3/profile/public/certificate)
PUBLIC_KEY_B64 = """
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAstWB95C5pHLXiYW59qyO4Xb+59KYVm9Hywbo77qETZVAyc6VIsxU+UWhd/k/YtjZibCznB+HaXWX9TVTFs9Nwgv7LRGq5uLczpZQDrU7dnGkl/urRA8p0Jv/f8T0MZdFWQgks91uFffeBmJOb58u68ZRxSYGMPe4hb9XXKDVsgoSJaRNYviH7RgAI2QhTCwLEiMqIaUX3p1SAc178ZlN8qHXSSGXvhDR1GKM+y2DIyJqlzfik7lD14mDY/I4lcbftib8cv7llkybtjX1AayfZp4XpmIXKWv8nRM488/jOAF81Bi13paKgpjQUUuwq9tb5Qd/DChytYgBTBTJFe7irDFCmTIcqPr8+IMB7tXA3YXPp3z605Z6cGoYxezUm2Nz2o6oUmarDUntDhq/PnkNergmSeSvS8gD9DHBuJkJWZweG3xOPXiKQAUBr92mdFhJGm6fitO5jsBxgpmulxpG0oKDy9lAOLWSqK92JMcbMNHn4wRikdI9HSiXrrI7fLhJYTbyU3I4v5ESdEsayHXuiwO/1C8y56egzKSw44GAtEpbAkTNEEfK5H5R0QnVBIXOvfeF4tzGvmkfOO6nNXU3o/WAdOyV3xSQ9dqLY5MEL4sJCGY1iJBIAQ452s8v0ynJG5Yq+8hNhsCVnklCzAlsIzQpnSVDUVEzv17grVAw078CAwEAAQ==
""".strip()


def get_public_key():
    """Load the ABDM RSA public key."""
    public_key_der = base64.b64decode(PUBLIC_KEY_B64)
    return load_der_public_key(public_key_der)


def encrypt_value(value: str) -> str:
    """
    Encrypt any value (Aadhaar, OTP, Mobile) using
    RSA/ECB/OAEPWithSHA-1AndMGF1Padding and return Base64 output.
    """
    value = value.strip()

    public_key = get_public_key()

    encrypted_bytes = public_key.encrypt(
        value.encode("utf-8"),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA1()),
            algorithm=hashes.SHA1(),
            label=None
        )
    )

    return base64.b64encode(encrypted_bytes).decode("utf-8")


if __name__ == "__main__":
    print("ABDM Encryption Utility")
    print("=" * 40)

    # Aadhaar
    aadhaar = input("Enter Aadhaar number (optional): ").strip()
    if aadhaar:
        print("\nEncrypted Aadhaar (use as loginId):")
        print(encrypt_value(aadhaar))

    # OTP
    otp = input("\nEnter OTP (optional): ").strip()
    if otp:
        print("\nEncrypted OTP (use as otpValue):")
        print(encrypt_value(otp))

    # Mobile
    mobile = input("\nEnter Mobile Number (optional): ").strip()
    if mobile:
        print("\nEncrypted Mobile (use as mobile):")
        print(encrypt_value(mobile))