"""
Policy router — determines which insurance policy baseline to use
based on extracted claim data.

Pure Python logic, no LLM call. This is a conditional edge in LangGraph.
"""
from typing import Dict, Any, Tuple
from app.config import DEFAULT_GOVT_POLICY, DEFAULT_PRIVATE_POLICY
from app.logger import get_logger

logger = get_logger(__name__)

# Government scheme markers (case-insensitive matching)
GOVT_SCHEME_MARKERS = {
    "pmjay", "ayushman bharat", "ab-pmjay", "ayushman",
    "rsby", "bpl", "rashtriya swasthya bima",
    "pradhan mantri", "jan arogya",
    # State schemes
    "mahatma jyotiba phule", "mjpjay",
    "chief minister", "cm health", "aarogyasri",
    "karunya", "kalaignar", "yeshasvini",
    "mukhyamantri amrutum", "ma yojana",
}

# Known private TPAs
PRIVATE_TPA_MARKERS = {
    "medi assist", "mediassist", "fhpl",
    "paramount health", "raksha tpa", "vidal health",
    "heritage health", "md india", "good health",
    "ericson", "anmol medicare", "medsave",
    "united health care", "park mediclaim",
    "genins india", "health india", "safeway",
    "east west assist", "dedicated healthcare",
}

# Known private insurers (non-exhaustive, top ones)
PRIVATE_INSURER_NAMES = {
    "star health", "max bupa", "niva bupa",
    "care health", "hdfc ergo", "bajaj allianz",
    "icici lombard", "new india assurance",
    "united india", "national insurance",
    "oriental insurance", "sbi health",
    "aditya birla health", "manipal cigna",
    "cholamandalam", "liberty general",
    "iffco tokio", "reliance general",
    "royal sundaram", "tata aig",
    "digit", "acko", "go digit",
}


def detect_policy_baseline(
    structured_data: Dict[str, Any],
) -> Tuple[str, str]:
    """
    Determine policy baseline from extracted claim data.

    Returns:
        Tuple of (policy_baseline, policy_type)
        - policy_baseline: company slug or default scheme name
        - policy_type: "government" or "private"
    """
    insurance = structured_data.get("insurance_details", {}) or {}
    company_name = (insurance.get("insurance_company") or "").strip().lower()
    tpa_name = (insurance.get("tpa_name") or "").strip().lower()
    scheme_indicators = [
        s.lower() for s in (insurance.get("scheme_indicators") or [])
    ]

    # ── Priority 1: Explicit company name found ──────────────────────
    if company_name:
        # Check if it's a government scheme name
        for marker in GOVT_SCHEME_MARKERS:
            if marker in company_name:
                logger.info(
                    f"[POLICY_ROUTER] Govt scheme detected via company name: "
                    f"'{company_name}' matched '{marker}'"
                )
                return DEFAULT_GOVT_POLICY, "government"

        # It's a named private insurer
        logger.info(
            f"[POLICY_ROUTER] Private insurer detected: '{company_name}'"
        )
        # Normalize to slug for downstream use
        slug = company_name.replace(" ", "_").replace(".", "")
        return slug, "private"

    # ── Priority 2: TPA name found ───────────────────────────────────
    if tpa_name:
        for marker in PRIVATE_TPA_MARKERS:
            if marker in tpa_name:
                logger.info(
                    f"[POLICY_ROUTER] Private TPA detected: "
                    f"'{tpa_name}' matched '{marker}'"
                )
                return DEFAULT_PRIVATE_POLICY, "private"

    # ── Priority 3: Scheme indicators ────────────────────────────────
    if scheme_indicators:
        all_indicators = " ".join(scheme_indicators)
        for marker in GOVT_SCHEME_MARKERS:
            if marker in all_indicators:
                logger.info(
                    f"[POLICY_ROUTER] Govt scheme detected via indicators: "
                    f"matched '{marker}'"
                )
                return DEFAULT_GOVT_POLICY, "government"

        for marker in PRIVATE_TPA_MARKERS:
            if marker in all_indicators:
                logger.info(
                    f"[POLICY_ROUTER] Private TPA detected via indicators: "
                    f"matched '{marker}'"
                )
                return DEFAULT_PRIVATE_POLICY, "private"

    # ── Fallback: Default to government ──────────────────────────────
    logger.info(
        "[POLICY_ROUTER] No company/TPA/scheme detected — "
        f"defaulting to '{DEFAULT_GOVT_POLICY}'"
    )
    return DEFAULT_GOVT_POLICY, "government"