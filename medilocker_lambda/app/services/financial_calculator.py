"""
Claim deduction risk calculator — deterministic Python logic.

LLM identifies WHICH errors exist → Python calculates how much of
the claim is at risk of rejection/reduction due to those errors.

The bill_amount is the TARGET. Errors cause deductions from that target.
Fix the errors → recover the full amount.
"""
from typing import Dict, Any, List
from datetime import datetime
from app.logger import get_logger

logger = get_logger(__name__)


DEDUCTION_RULES = {
    # ── Full rejection (100% at risk) ──
    "primary_diagnosis_missing": {
        "pct": 1.00,
        "label": "Primary diagnosis missing",
        "explain": "TPA will reject entire claim without a valid diagnosis",
    },
    "date_inconsistency": {
        "pct": 1.00,
        "label": "Discharge date before admission date",
        "explain": "Illogical dates cause automatic rejection",
    },
    "insurance_company_missing": {
        "pct": 1.00,
        "label": "Insurance company name not specified",
        "explain": "Claim cannot be processed without insurer name",
    },
    "policy_number_missing": {
        "pct": 1.00,
        "label": "Policy number missing",
        "explain": "No policy number = no claim processing",
    },

    # ── Major partial deductions ──
    "pre_existing_as_primary": {
        "pct": 0.60,
        "label": "Pre-existing condition as primary diagnosis",
        "explain": "TPA flags as pre-existing — typically 60% deduction",
    },
    "vague_diagnosis": {
        "pct": 0.40,
        "label": "Vague primary diagnosis (fever, weakness, etc.)",
        "explain": "TPAs reject vague diagnoses — ~40% risk",
    },
    "missing_documents": {
        "pct": 0.30,
        "label": "Required documents not submitted",
        "explain": "Missing investigation reports or prescriptions — ~30% deduction",
    },
    "icd_diagnosis_mismatch": {
        "pct": 0.25,
        "label": "ICD code doesn't match stated diagnosis",
        "explain": "Code-diagnosis mismatch — ~25% haircut",
    },
    "room_rent_exceeds_cap": {
        "pct": 0.20,
        "label": "Room rent may exceed policy cap",
        "explain": "Triggers proportional deduction on entire claim — ~20%",
    },

    # ── Medium deductions ──
    "invalid_icd_format": {
        "pct": 0.15,
        "label": "Invalid ICD-10 code format",
        "explain": "Malformed code triggers manual review — ~15% risk",
    },
    "unjustified_icu_days": {
        "pct": 0.15,
        "label": "ICU days without clinical justification",
        "explain": "Unjustified ICU gets reduced — ~15%",
    },
    "nature_of_illness_unchecked": {
        "pct": 0.10,
        "label": "Illness/Injury/Maternity not marked",
        "explain": "Mandatory classification missing — ~10% risk",
    },
    "branded_drugs_without_justification": {
        "pct": 0.10,
        "label": "Branded drugs without generic-unavailable note",
        "explain": "TPA may deny branded drug portion — ~10%",
    },

    # ── Cross-doc mismatches (only when extra docs provided) ──
    "bill_claim_amount_mismatch": {
        "pct": 0.15,
        "label": "Hospital bill total doesn't match claim form amount",
        "explain": "Amount discrepancy between bill and claim form — ~15%",
    },
    "diagnosis_mismatch_across_docs": {
        "pct": 0.20,
        "label": "Diagnosis differs between claim form and hospital bill/prescription",
        "explain": "Inconsistent diagnosis across documents — ~20% risk",
    },
    "charges_in_bill_not_in_claim": {
        "pct": 0.10,
        "label": "Line items in hospital bill not claimed in form",
        "explain": "Claimable charges present in bill but not in claim — ~10% underclaim",
    },
    "prescription_treatment_mismatch": {
        "pct": 0.15,
        "label": "Prescription medicines don't match billed pharmacy charges",
        "explain": "Mismatch between prescribed and billed drugs — ~15%",
    },

    # ── Minor / delay risks ──
    "gender_missing": {
        "pct": 0.05,
        "label": "Patient gender not specified",
        "explain": "Processing delay — minor deduction risk",
    },
    "hospital_registration_missing": {
        "pct": 0.05,
        "label": "Hospital registration number missing",
        "explain": "Triggers verification delay",
    },

    # ── Payment blocks (0% deduction but blocks disbursement) ──
    "ifsc_invalid": {
        "pct": 0.0,
        "is_payment_block": True,
        "label": "Invalid IFSC code",
        "explain": "Payment cannot be disbursed with invalid IFSC",
    },
    "account_name_mismatch": {
        "pct": 0.0,
        "is_payment_block": True,
        "label": "Bank account holder name doesn't match patient",
        "explain": "Payment held until name verification",
    },
}


def _parse_amount(value):
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace("₹", "").replace(",", "").replace(" ", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0
    return 0.0


def calculate_deduction_risk(
    structured_data: Dict[str, Any],
    identified_errors: List[str],
) -> Dict[str, Any]:
    """
    Calculate how much of the claim is at risk due to errors.

    Returns:
        bill_amount: target (what patient deserves)
        at_risk_amount: what will be deducted if submitted as-is
        expected_approval_as_is: bill - at_risk
        recoverable_amount: what they get back by fixing errors
        deduction_breakdown: each error + its rupee impact
    """
    claim = structured_data.get("claim_details", {}) or {}
    bill_amount = _parse_amount(claim.get("bill_amount"))
    claimed_amount = _parse_amount(claim.get("claimed_amount"))

    target = bill_amount if bill_amount > 0 else claimed_amount

    if target == 0:
        return {
            "bill_amount": 0,
            "at_risk_amount": 0,
            "expected_approval_as_is": 0,
            "recoverable_amount": 0,
            "deduction_breakdown": [],
            "payment_blocks": [],
            "summary": "Cannot calculate — no bill amount found",
        }

    deduction_breakdown = []
    payment_blocks = []
    has_full_rejection = False
    cumulative_pct = 0.0

    for err_code in identified_errors:
        rule = DEDUCTION_RULES.get(err_code)
        if not rule:
            logger.warning(f"[FIN_CALC] Unknown error: {err_code}")
            continue

        if rule.get("is_payment_block"):
            payment_blocks.append({
                "error": err_code,
                "label": rule["label"],
                "explain": rule["explain"],
            })
            continue

        if rule["pct"] >= 1.0:
            has_full_rejection = True

        deduction_amount = round(target * rule["pct"], 2)
        cumulative_pct += rule["pct"]

        deduction_breakdown.append({
            "error": err_code,
            "label": rule["label"],
            "explain": rule["explain"],
            "deduction_amount": deduction_amount,
        })

    # Calculate at-risk amount
    if has_full_rejection:
        # Even full-rejection errors don't always mean ₹0 approval.
        # TPAs often process partial claims or return for correction.
        # Cap at 85% risk — patient typically gets at least 15% through.
        at_risk = round(target * 0.85, 2)
    else:
        effective_pct = min(cumulative_pct, 0.80)
        at_risk = round(target * effective_pct, 2)

    # Minimum expected approval — never show ₹0
    # Even the worst forms get some basic processing
    minimum_approval = round(target * 0.10, 2)  # at least 10% of bill
    expected_as_is = round(max(minimum_approval, target - at_risk), 2)

    # Build summary
    if len(deduction_breakdown) == 0 and len(payment_blocks) == 0:
        summary = (
            f"Your claim form looks clean. You should receive the full "
            f"₹{target:,.0f} from the TPA."
        )
    else:
        pct = (at_risk / target * 100) if target > 0 else 0
        summary = (
            f"Your bill is ₹{target:,.0f}. Due to {len(deduction_breakdown)} error(s), "
            f"approximately ₹{at_risk:,.0f} ({pct:.0f}%) is at risk of rejection. "
            f"If submitted as-is, you will likely receive only ₹{expected_as_is:,.0f}. "
            f"Fix the errors below to recover the full ₹{target:,.0f}."
        )
        if payment_blocks:
            summary += (
                f" Additionally, {len(payment_blocks)} issue(s) may block "
                f"payment disbursement."
            )

    result = {
        "bill_amount": target,
        "at_risk_amount": at_risk,
        "expected_approval_as_is": expected_as_is,
        "recoverable_amount": round(target - expected_as_is, 2),
        "deduction_breakdown": deduction_breakdown,
        "payment_blocks": payment_blocks,
        "summary": summary,
    }

    logger.info(
        f"[FIN_CALC] target=₹{target}, at_risk=₹{at_risk}, "
        f"expected=₹{expected_as_is}, errors={len(identified_errors)}"
    )
    return result