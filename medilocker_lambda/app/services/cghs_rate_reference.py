"""
CGHS Rate Reference Module for Kokoro MediLocker
=================================================
Provides deterministic, rule-based cost estimates for the Pre-Auth
cashless-request cost fields (room rent, ICU charges, OT charges,
professional fees, medicines/consumables) when these are not explicitly
present in the source documents — which is almost always the case, since
these are hospital pre-treatment estimates, not something a prescription
or insurance policy would state.

IMPORTANT — what these numbers are and are not:
    - PROCEDURE_RATE_CARD values are official CGHS (Central Government
      Health Scheme) NABH-tier package rates, effective 13 October 2025,
      as published by the Ministry of Health & Family Welfare and
      referenced via the official CGHS rate list (cghs.mohfw.gov.in).
      These are a defensible, government-sourced reference point — they
      are NOT the hospital's actual/final charge, and are not a market
      or private-hospital rate.
    - COST_SPLIT_RATIOS below is NOT a government-published figure. It is
      a reasonable, documented assumption for how a CGHS lump-sum package
      rate typically breaks down into line items, used only because the
      Star Health form requires itemized fields while CGHS publishes a
      single package total. Tune this if real hospital cost-structure
      data becomes available.
    - Every value returned by estimate_procedure_costs() must be treated
      as an ESTIMATE, not a confirmed figure. Callers must flag it as such
      downstream (see cost_estimate_applied / cost_estimate_source in
      claim_validator_graph.py) rather than presenting it as extracted
      fact.

Architecture (mirrors icd_lookup.py's determinism-first pattern):
    Extracted procedure name -> rapidfuzz match against PROCEDURE_RATE_CARD
    -> if confident match, split the package total into line items using
    COST_SPLIT_RATIOS and per-day room/ICU rates -> return estimate dict.
    No LLM involvement; if no confident match is found, returns None and
    the caller must leave the fields blank rather than guess.

Dependencies:
    - rapidfuzz (already a project dependency, used by icd_lookup.py)
"""

import logging
from typing import Optional, Dict, Any

from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)

# ============================================================================
# CGHS NABH-TIER PACKAGE RATES
# Source: CGHS Rate List, effective 13 October 2025 (notified 3 Oct 2025),
# Ministry of Health & Family Welfare, Government of India.
# All values are lump-sum package totals in INR (Tier X / metro, NABH,
# semi-private ward baseline).
# ============================================================================

PROCEDURE_RATE_CARD: Dict[str, int] = {
    # --- Gynecology / Obstetrics ---
    "hysteroscopic polypectomy": 9200,
    "transcervical hysteroscopic polypectomy": 9200,
    "polypectomy": 9200,  # standalone — extraction commonly returns just this
                           # word (with "hysteroscopy" as a separate field)
    "dilatation and curettage": 13400,
    "d and c": 13400,
    "diagnostic hysterolaparoscopy": 20000,
    "diagnostic laparoscopy": 20000,
    "normal delivery": 35000,
    "normal delivery with or without episiotomy": 35000,
    "caesarean section": 53000,
    "cesarean section": 53000,
    "lscs": 53000,
    "abdominal hysterectomy": 43000,
    "vaginal hysterectomy": 43000,
    "total laparoscopic hysterectomy": 63000,
    "myomectomy open": 35000,
    "myomectomy": 35000,
    "myomectomy laparoscopic": 43000,
    "laparoscopic myomectomy": 43000,
    "ovarian cystectomy": 43000,
    "laparoscopic ovarian cystectomy": 43000,

    # --- General Surgery ---
    "appendicectomy": 43000,
    "appendectomy": 43000,
    "cholecystectomy laparoscopic": 43000,
    "laparoscopic cholecystectomy": 43000,
    "cholecystectomy open": 35000,
    "cholecystectomy": 43000,
    "inguinal hernia repair": 35000,
    "inguinal hernia": 35000,
    "umbilical hernia repair": 27500,
    "umbilical hernia": 27500,
}

# Documented split-ratio ASSUMPTION (not itself a government figure) — see
# module docstring. Remainder after these ratios is implicitly attributed
# to room/nursing/misc, which is instead computed separately below via a
# per-day room rate multiplied by expected length of stay.
COST_SPLIT_RATIOS: Dict[str, float] = {
    "professional_fees": 0.35,
    "ot_charges": 0.12,
    "medicines_consumables": 0.18,
    "investigation_diagnostic": 0.10,
}

# Reference per-day rates (semi-private ward baseline).
DEFAULT_ROOM_RENT_PER_DAY = 1500
DEFAULT_ICU_CHARGES_PER_DAY = 5400  # sourced from CGHS ICU-charges circular

DEFAULT_EXPECTED_DAYS_STAY = 2

MATCH_CONFIDENCE_THRESHOLD = 70  # rapidfuzz score (0-100); below this, no match

SOURCE_LABEL = (
    "CGHS NABH reference rate (effective 13 Oct 2025) — estimate only, "
    "not the hospital's actual charge"
)

_rate_card_keys = list(PROCEDURE_RATE_CARD.keys())


def _round_to_nearest_hundred(value: float) -> int:
    return int(round(value / 100.0)) * 100


def estimate_procedure_costs(
    procedure_name: Optional[str],
    expected_days_stay: Optional[int] = None,
    days_in_icu: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Fuzzy-matches procedure_name against PROCEDURE_RATE_CARD and, on a
    confident match, splits the CGHS package total into line-item cost
    estimates for the Pre-Auth cashless-request section.

    Returns None if procedure_name is empty or no match clears
    MATCH_CONFIDENCE_THRESHOLD — callers must leave fields blank in that
    case rather than guessing.

    All monetary values are rounded to the nearest Rs. 100.
    """
    if not procedure_name or not str(procedure_name).strip():
        return None

    query = str(procedure_name).strip().lower()

    match = process.extractOne(
        query,
        _rate_card_keys,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=MATCH_CONFIDENCE_THRESHOLD,
    )

    if not match:
        logger.info(
            f"[CGHS_RATE] No confident rate-card match for procedure="
            f"'{procedure_name}' (threshold={MATCH_CONFIDENCE_THRESHOLD})"
        )
        return None

    matched_key, score, _ = match
    package_total = PROCEDURE_RATE_CARD[matched_key]

    days = expected_days_stay if expected_days_stay and expected_days_stay > 0 else DEFAULT_EXPECTED_DAYS_STAY
    icu_days = days_in_icu if days_in_icu and days_in_icu > 0 else 0

    room_rent_per_day = DEFAULT_ROOM_RENT_PER_DAY
    icu_charges = _round_to_nearest_hundred(DEFAULT_ICU_CHARGES_PER_DAY * icu_days)
    room_total = _round_to_nearest_hundred(room_rent_per_day * days)

    professional_fees = _round_to_nearest_hundred(package_total * COST_SPLIT_RATIOS["professional_fees"])
    ot_charges = _round_to_nearest_hundred(package_total * COST_SPLIT_RATIOS["ot_charges"])
    medicines_consumables = _round_to_nearest_hundred(package_total * COST_SPLIT_RATIOS["medicines_consumables"])
    investigation_cost = _round_to_nearest_hundred(package_total * COST_SPLIT_RATIOS["investigation_diagnostic"])

    total_expected_cost = (
        room_total + icu_charges + professional_fees + ot_charges
        + medicines_consumables + investigation_cost
    )

    logger.info(
        f"[CGHS_RATE] '{procedure_name}' -> matched '{matched_key}' "
        f"(score={score:.0f}), package_total={package_total}, "
        f"total_expected_cost={total_expected_cost}"
    )

    return {
        "matched_procedure": matched_key,
        "match_confidence": round(score / 100.0, 2),
        "room_rent_per_day": room_rent_per_day,
        "icu_charges": icu_charges,
        "ot_charges": ot_charges,
        "professional_fees": professional_fees,
        "medicines_consumables": medicines_consumables,
        "investigation_cost": investigation_cost,
        "total_expected_cost": total_expected_cost,
        "source": SOURCE_LABEL,
    }


TEXT_SCAN_CONFIDENCE_THRESHOLD = 80  # rapidfuzz token_set_ratio score (0-100)


def find_procedure_in_text(raw_text: Optional[str]) -> Optional[str]:
    """
    Deterministic fallback for when structured extraction didn't populate
    procedure_1 (or any diagnosis field) with a recognizable procedure name,
    but the raw OCR text of the source document actually mentions one
    (e.g. a doctor's handwritten "Adv: Polypectomy c hysteroscopy" note that
    the LLM's structured extraction missed).

    Scans raw_text line-by-line against PROCEDURE_RATE_CARD keys using
    token_set_ratio (order-independent — handles cases like a prescription
    reading "Polypectomy c hysteroscopy" matching the rate-card key
    "hysteroscopic polypectomy" despite the reversed word order). Returns
    the matched rate-card key, or None if nothing clears
    TEXT_SCAN_CONFIDENCE_THRESHOLD — never guesses.
    """
    if not raw_text or not str(raw_text).strip():
        return None

    lines = [l.strip() for l in str(raw_text).lower().split("\n") if l.strip()]
    if not lines:
        return None

    best_key = None
    best_score = 0.0
    for key in _rate_card_keys:
        for line in lines:
            score = fuzz.token_set_ratio(key, line)
            if score > best_score:
                best_score = score
                best_key = key

    if best_key and best_score >= TEXT_SCAN_CONFIDENCE_THRESHOLD:
        logger.info(
            f"[CGHS_RATE] Raw-text scan matched '{best_key}' "
            f"(score={best_score:.0f}) in OCR text"
        )
        return best_key

    return None
