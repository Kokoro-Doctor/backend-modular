"""
ICD-10 Deterministic Lookup Module for Kokoro MediLocker
========================================================
Replaces LLM-based ICD code generation with verified database lookups.

Architecture:
    LLM extracts diagnosis text → Synonym map (colloquial → medical) →
    Multi-step search (exact substring → fuzzy fallback) → Verified ICD code

Data Sources:
    - WHO ICD-10 (12,246 codes) — Primary, used by Indian hospitals
    - ICD-10-CM (69,612 codes) — Secondary, for granularity

Dependencies:
    - simple-icd-10 (WHO ICD-10 database, bundled as pip package)
    - simple-icd-10-cm (ICD-10-CM database, bundled as pip package)
    - rapidfuzz (fast fuzzy string matching)

Lambda Deployment:
    Add to Lambda Layer: simple-icd-10, simple-icd-10-cm, rapidfuzz
    Total size: ~5MB (all pure Python + small C extension for rapidfuzz)
"""

import re
import json
import logging
from typing import Optional
from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)

# ============================================================================
# ICD-10 DATABASE LOADER
# ============================================================================

_icd_db = None          # {code: description}
_norm_db = None         # {code: normalized_description}
_norm_descs = None      # [normalized descriptions] for rapidfuzz
_norm_codes = None      # [codes] parallel to _norm_descs


def _load_database():
    """Load WHO ICD-10 + ICD-10-CM into memory. Called once on first use."""
    global _icd_db, _norm_db, _norm_descs, _norm_codes

    if _icd_db is not None:
        return

    import simple_icd_10 as icd
    import simple_icd_10_cm as cm

    db = {}

    # WHO ICD-10 (primary — India uses WHO codes)
    ROMAN = {"I","II","III","IV","V","VI","VII","VIII","IX","X",
             "XI","XII","XIII","XIV","XV","XVI","XVII","XVIII","XIX","XX","XXI","XXII"}
    for code in icd.get_all_codes():
        if code in ROMAN:
            continue
        if "-" in code and code[0].isalpha():   # range headers like "A00-A09"
            continue
        db[code] = icd.get_description(code)

    # ICD-10-CM billable codes (secondary — adds granularity)
    for code in cm.get_all_codes():
        if code not in db and cm.is_leaf(code):
            db[code] = cm.get_description(code)

    _icd_db = db
    _norm_db = {code: _normalize(desc) for code, desc in db.items()}
    _norm_descs = list(_norm_db.values())
    _norm_codes = list(_norm_db.keys())

    logger.info(f"ICD-10 database loaded: {len(db)} codes")


# ============================================================================
# TEXT NORMALIZATION
# ============================================================================

_SPELLING_MAP = {
    "haemorrhage": "hemorrhage",
    "haemorrhoid": "hemorrhoid",
    "haemorrhagic": "hemorrhagic",
    "anaemia": "anemia",
    "oedema": "edema",
    "oesophag": "esophag",
    "diarrhoea": "diarrhea",
    "leukaemia": "leukemia",
    "foetal": "fetal",
    "foetus": "fetus",
    "gynaec": "gynec",
    "caesarean": "cesarean",
    "tumour": "tumor",
    "labour": "labor",
    "coeliac": "celiac",
    "paediatric": "pediatric",
}


def _normalize(text: str) -> str:
    """Normalize text for matching: lowercase, strip parentheticals, unify spelling."""
    t = text.lower().strip()
    t = re.sub(r'\([^)]*\)', '', t)     # remove (parenthetical)
    t = re.sub(r'\[[^\]]*\]', '', t)     # remove [bracketed]
    t = re.sub(r'\s+', ' ', t).strip()   # collapse whitespace
    for old, new in _SPELLING_MAP.items():
        t = t.replace(old, new)
    return t


# ============================================================================
# INDIAN MEDICAL SYNONYM MAP
# ============================================================================
# Colloquial / abbreviated terms → standard medical terminology
# LLM extracts "heart attack" → synonym map converts to "acute myocardial infarction"
# → rapidfuzz matches against ICD-10 database

INDIAN_MEDICAL_SYNONYMS = {
    # --- Cardiovascular ---
    "heart attack":       ["acute myocardial infarction"],
    "heart failure":      ["heart failure"],
    "heart block":        ["atrioventricular block"],
    "chest pain":         ["angina pectoris"],
    "high bp":            ["essential hypertension"],
    "low bp":             ["hypotension"],
    "bp":                 ["essential hypertension"],
    "blood pressure":     ["essential hypertension"],
    "brain stroke":       ["cerebral infarction"],
    "stroke":             ["cerebral infarction"],
    "paralysis":          ["hemiplegia"],

    # --- Renal ---
    "kidney stone":       ["calculus of kidney"],
    "kidney failure":     ["chronic kidney disease"],
    "dialysis":           ["chronic kidney disease stage 5"],
    "blood in urine":     ["hematuria"],

    # --- GI / Liver ---
    "gallbladder stone":  ["cholelithiasis"],
    "gallstone":          ["cholelithiasis"],
    "piles":              ["hemorrhoids"],
    "fissure":            ["anal fissure"],
    "loose motions":      ["diarrhea"],
    "loose stools":       ["diarrhea"],
    "acidity":            ["gastroesophageal reflux disease"],
    "gas":                ["dyspepsia"],
    "ulcer":              ["peptic ulcer"],
    "jaundice":           ["jaundice"],
    "liver problem":      ["liver disease"],
    "appendix":           ["acute appendicitis"],
    "appendix operation": ["acute appendicitis"],

    # --- Endocrine ---
    "sugar":              ["type 2 diabetes mellitus"],
    "diabetes":           ["diabetes mellitus"],
    "thyroid":            ["disorder of thyroid gland"],

    # --- Infections ---
    "tb":                 ["tuberculosis of lung"],
    "typhoid":            ["typhoid fever"],
    "malaria":            ["malaria"],
    "dengue":             ["dengue"],
    "chikungunya":        ["chikungunya virus disease"],
    "covid":              ["covid-19"],
    "corona":             ["covid-19"],
    "chickenpox":         ["varicella"],
    "measles":            ["measles"],
    "mumps":              ["mumps"],

    # --- Respiratory ---
    "asthma":             ["asthma"],
    "pneumonia":          ["pneumonia"],
    "sinus":              ["sinusitis"],
    "breathlessness":     ["dyspnea"],
    "wheezing":           ["wheezing"],
    "snoring":            ["sleep apnea"],
    "sleep apnea":        ["sleep apnea"],

    # --- Urogenital ---
    "uti":                ["urinary tract infection"],
    "urinary infection":  ["urinary tract infection"],
    "prostate":           ["hyperplasia of prostate"],

    # --- Gynecological ---
    "pcod":               ["polycystic ovarian syndrome"],
    "pcos":               ["polycystic ovarian syndrome"],
    "fibroids":           ["leiomyoma of uterus"],
    "endometriosis":      ["endometriosis"],
    "ectopic pregnancy":  ["ectopic pregnancy"],
    "miscarriage":        ["spontaneous abortion"],
    "c-section":          ["single delivery by cesarean section"],
    "cesarean":           ["single delivery by cesarean section"],
    "normal delivery":    ["single spontaneous delivery"],

    # --- Musculoskeletal ---
    "slip disc":          ["intervertebral disc disorder"],
    "slipped disc":       ["intervertebral disc disorder"],
    "frozen shoulder":    ["adhesive capsulitis of shoulder"],
    "knee replacement":   ["gonarthrosis"],
    "hip replacement":    ["coxarthrosis"],
    "fracture":           ["fracture"],
    "ligament tear":      ["sprain and strain"],
    "acl tear":           ["anterior cruciate ligament injury"],
    "sciatica":           ["sciatica"],
    "arthritis":          ["arthritis"],
    "gout":               ["gout"],
    "uric acid":          ["gout"],

    # --- Neurological ---
    "fits":               ["epilepsy"],
    "seizures":           ["epilepsy"],
    "convulsions":        ["epilepsy"],
    "migraine":           ["migraine"],
    "vertigo":            ["vertigo"],
    "headache":           ["headache"],

    # --- Eye ---
    "cataract":           ["cataract"],

    # --- Dermatological ---
    "skin allergy":       ["allergic contact dermatitis"],
    "eczema":             ["dermatitis"],
    "psoriasis":          ["psoriasis"],

    # --- Hematological ---
    "anemia":             ["anemia"],
    "blood cancer":       ["leukemia"],
    "cancer":             ["malignant neoplasm"],

    # --- Cardiac procedures (for context) ---
    "stent":              ["coronary angioplasty"],
    "bypass surgery":     ["coronary artery bypass"],
    "angioplasty":        ["percutaneous coronary intervention"],
    "pacemaker":          ["presence of cardiac pacemaker"],
}


# ============================================================================
# CHAPTER PREFERENCE HINTS
# ============================================================================
# When a standalone disease name matches multiple chapters, prefer the
# primary chapter for that disease category.

_CHAPTER_HINTS = {
    "pneumonia":        ["J"],          # Respiratory
    "hypertension":     ["I"],          # Circulatory
    "gastroenteritis":  ["A", "K"],     # Infectious / Digestive
    "diabetes":         ["E"],          # Endocrine
    "fracture":         ["S"],          # Injury
    "anemia":           ["D"],          # Blood
    "cataract":         ["H"],          # Eye
    "tuberculosis":     ["A"],          # Infectious
    "epilepsy":         ["G"],          # Nervous system
    "arthritis":        ["M"],          # Musculoskeletal
    "hernia":           ["K"],          # Digestive
    "asthma":           ["J"],          # Respiratory
}

# Descriptions containing these words are deprioritized for general queries
_OBSTETRIC_PATTERNS = frozenset([
    "pregnancy", "childbirth", "puerperium", "neonatal", "perinatal",
    "fetus", "newborn", "complicating", "obstetric", "trimester",
    "postpartum", "antepartum", "intrapartum", "following delivery",
    "in labor",
])

_OBSTETRIC_QUERY_WORDS = frozenset([
    "pregnan", "neonatal", "baby", "newborn", "maternal",
    "obstetric", "delivery", "cesarean", "labour", "labor",
])


def _is_obstetric_desc(desc: str) -> bool:
    return any(p in desc for p in _OBSTETRIC_PATTERNS)


# ============================================================================
# CORE SEARCH ENGINE
# ============================================================================

def _search_medical_term(query: str, limit: int = 5) -> list:
    """
    Multi-step ICD-10 search:
      Step 1: Exact substring match (highest precision)
      Step 2: Fuzzy fallback via rapidfuzz (when no substring match)

    Returns list of (code, description, score) tuples.
    """
    _load_database()

    q = _normalize(query)
    q_words = set(q.split())
    q_word_count = len(q.split())
    is_obstetric = bool(q_words & _OBSTETRIC_QUERY_WORDS)

    # Determine chapter preference
    preferred_chapters = []
    for term, chapters in _CHAPTER_HINTS.items():
        if term in q:
            preferred_chapters = chapters
            break

    # ---- Step 1: Exact substring matches ----
    exact_candidates = []
    for code, desc in _norm_db.items():
        if q not in desc:
            continue

        sort_score = fuzz.token_sort_ratio(q, desc)
        bonus, penalty = 0, 0

        # Bonus: description starts with query (strongest relevance signal)
        if desc.startswith(q):
            bonus += 15
        elif q in desc.split(",")[0]:
            bonus += 8

        # Penalty: obstetric/neonatal codes for non-obstetric queries
        if not is_obstetric and _is_obstetric_desc(desc):
            penalty += 50

        # Bonus: preferred chapter match
        if preferred_chapters and any(code.startswith(ch) for ch in preferred_chapters):
            bonus += 10

        # Bonus: "unspecified" variants for short/standalone queries
        if q_word_count <= 2 and "unspecified" in desc:
            bonus += 10

        exact_candidates.append((code, _icd_db[code], sort_score + bonus - penalty))

    if exact_candidates:
        exact_candidates.sort(key=lambda x: x[2], reverse=True)
        return exact_candidates[:limit]

    # ---- Step 2: Fuzzy fallback ----
    results = process.extract(q, _norm_descs, scorer=fuzz.token_sort_ratio, limit=limit * 3)
    fuzzy_candidates = []
    for desc, score, idx in results:
        code = _norm_codes[idx]
        bonus, penalty = 0, 0

        if not is_obstetric and _is_obstetric_desc(_norm_db[code]):
            penalty += 50
        if preferred_chapters and any(code.startswith(ch) for ch in preferred_chapters):
            bonus += 10
        if _norm_db[code].startswith(q):
            bonus += 15

        fuzzy_candidates.append((code, _icd_db[code], score + bonus - penalty))

    fuzzy_candidates.sort(key=lambda x: x[2], reverse=True)
    return fuzzy_candidates[:limit]


# ============================================================================
# PUBLIC API
# ============================================================================

def lookup_icd_code(
    diagnosis_text: str,
    llm_suggested_code: Optional[str] = None,
    limit: int = 3
) -> dict:
    """
    Deterministic ICD-10 code lookup with optional LLM verification.

    Args:
        diagnosis_text: Diagnosis as extracted by LLM (can be colloquial or medical)
        llm_suggested_code: ICD code suggested by LLM (optional, for dual verification)
        limit: Number of alternative codes to return

    Returns:
        {
            "code": "A91",
            "description": "Dengue hemorrhagic fever",
            "confidence": 0.96,
            "method": "synonym→search→confirmed",
            "alternatives": [("A97.2", "Severe Dengue"), ...],
            "llm_code_overridden": None or "A97.1"
        }
    """
    if not diagnosis_text or not diagnosis_text.strip():
        return {
            "code": None, "description": None, "confidence": 0.0,
            "method": "empty_input", "alternatives": [],
            "llm_code_overridden": None
        }

    # Step 1: Synonym resolution (colloquial → medical term)
    q_lower = diagnosis_text.lower().strip()
    search_term = diagnosis_text
    synonym_used = False

    if q_lower in INDIAN_MEDICAL_SYNONYMS:
        search_term = INDIAN_MEDICAL_SYNONYMS[q_lower][0]
        synonym_used = True
        logger.debug(f"Synonym: '{diagnosis_text}' → '{search_term}'")

    # Step 2: Search ICD-10 database
    results = _search_medical_term(search_term, limit=limit + 2)

    if not results:
        return {
            "code": llm_suggested_code,   # fall back to LLM if no DB match
            "description": None,
            "confidence": 0.3 if llm_suggested_code else 0.0,
            "method": "no_db_match" + ("_llm_fallback" if llm_suggested_code else ""),
            "alternatives": [],
            "llm_code_overridden": None
        }

    top_code, top_desc, top_score = results[0]
    confidence = min(top_score / 100.0, 1.0)

    # Step 3: Dual verification against LLM suggestion
    verification = "db_only"
    llm_overridden = None

    if llm_suggested_code:
        llm_clean = llm_suggested_code.strip().upper()
        top_clean = top_code.strip().upper()

        # Check exact match OR parent-child relationship
        if (llm_clean == top_clean or
            llm_clean.startswith(top_clean) or
            top_clean.startswith(llm_clean)):
            verification = "confirmed"
            confidence = min(confidence + 0.1, 1.0)
        else:
            verification = "corrected"
            llm_overridden = llm_suggested_code

    method_parts = []
    if synonym_used:
        method_parts.append("synonym")
    method_parts.append("search")
    method_parts.append(verification)

    return {
        "code": top_code,
        "description": top_desc,
        "confidence": round(confidence, 2),
        "method": "→".join(method_parts),
        "alternatives": [(c, d) for c, d, _ in results[1:limit]],
        "llm_code_overridden": llm_overridden
    }


def batch_lookup(diagnoses: list, llm_codes: Optional[dict] = None) -> list:
    """
    Look up ICD codes for multiple diagnoses at once.

    Args:
        diagnoses: List of diagnosis strings
        llm_codes: Optional dict mapping diagnosis_text → llm_suggested_code

    Returns:
        List of lookup results (same format as lookup_icd_code)
    """
    llm_codes = llm_codes or {}
    return [
        lookup_icd_code(d, llm_codes.get(d))
        for d in diagnoses
    ]


def validate_icd_code(code: str) -> dict:
    """
    Check if a given ICD-10 code exists in the database.

    Returns:
        {"valid": True/False, "description": "..." or None, "source": "WHO"/"CM"/None}
    """
    _load_database()
    if code in _icd_db:
        return {"valid": True, "description": _icd_db[code], "source": "database"}
    return {"valid": False, "description": None, "source": None}


# ============================================================================
# CLI TESTING
# ============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("ICD-10 Deterministic Lookup — Test Suite")
    print("=" * 70)

    test_cases = [
        # (diagnosis, llm_code, expected_prefix)
        ("Dengue hemorrhagic fever", "A91", "A91"),
        ("Dengue fever", "A90", "A90"),
        ("Typhoid fever", "A01.0", "A01.0"),
        ("Acute appendicitis", "K35.9", "K35"),
        ("Type 2 diabetes mellitus", "E11", "E11"),
        ("Essential hypertension", "I10", "I10"),
        ("Pneumonia", "J18.9", "J"),
        ("Urinary tract infection", "N39.0", "N39.0"),
        ("Chronic kidney disease", "N18", "N18"),
        ("Cerebral infarction", "I63", "I63"),
        # Colloquial inputs (no LLM code)
        ("heart attack", None, "I21"),
        ("kidney stone", None, "N20"),
        ("sugar", None, "E11"),
        ("piles", None, "K64"),
        ("UTI", None, "N39"),
        ("tb", None, "A15"),
        ("pcod", None, "E28"),
        ("c-section", None, "O82"),
        ("slip disc", None, "M51"),
        ("bp", None, "I10"),
    ]

    passed = 0
    for diagnosis, llm_code, expected in test_cases:
        result = lookup_icd_code(diagnosis, llm_code)
        ok = result["code"] and result["code"].startswith(expected)
        if ok:
            passed += 1
        status = "✅" if ok else "❌"
        override = f" [LLM {result['llm_code_overridden']} overridden]" if result["llm_code_overridden"] else ""
        print(f"{status} '{diagnosis}' → {result['code']}: {result['description'][:50]}  "
              f"({result['confidence']:.0%}, {result['method']}){override}")

    print(f"\n{'🎉 ALL PASSED!' if passed == len(test_cases) else f'{passed}/{len(test_cases)} passed'}")