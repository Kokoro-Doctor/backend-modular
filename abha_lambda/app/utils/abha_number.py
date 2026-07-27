"""
ABHA number formatting helpers.

An ABHA number is 14 digits. ABDM presents it in two different forms depending
on the API, and callers (Postman, front-end) send it inconsistently too:

  - identity / profile APIs return it dashed:            "91-4118-0337-7265"
  - HIP link APIs (generate-token, care-context) want    "91411803377265"
    it as bare 14 digits, no dashes

We key the AbhaAccounts table on the dashed form, because that is what ABDM
returns on create / login (see abha_accounts_service PK docstring). To stay
consistent no matter how a caller supplies the number, normalize at both
boundaries instead of trusting the raw input:

  - to_storage_key(): canonical dashed form used as the DynamoDB PK
  - to_abdm_digits(): bare 14 digits sent in ABDM link-API payloads
"""
import re


def _digits(abha_number: str) -> str:
    """Strip everything that isn't a digit."""
    return re.sub(r"\D", "", abha_number or "")


def to_abdm_digits(abha_number: str) -> str:
    """Bare 14-digit form for ABDM link-API payloads (no dashes)."""
    return _digits(abha_number)


def to_storage_key(abha_number: str) -> str:
    """
    Canonical dashed form (XX-XXXX-XXXX-XXXX) used as the AbhaAccounts PK.

    Accepts input with or without dashes. If the value isn't a clean 14-digit
    number it is returned unchanged, so an unexpected format misses the lookup
    loudly instead of being silently reformatted into a wrong key.
    """
    digits = _digits(abha_number)
    if len(digits) != 14:
        return abha_number
    return f"{digits[0:2]}-{digits[2:6]}-{digits[6:10]}-{digits[10:14]}"
