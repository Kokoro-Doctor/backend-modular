"""
All prompts for the claim validation pipeline.
Separated from logic so they can be iterated on independently.
"""

# ── Node 2: Field Extraction (from claim form only) ──────────────────
EXTRACTION_SYSTEM = (
    "You extract structured insurance/claim data from OCR text. "
    "Return strict JSON only. No markdown, no explanation."
)

EXTRACTION_USER = """You are an insurance claim data extraction system.

Extract ONLY explicitly present information from the document.

STRICT RULES:
1. Do NOT infer or assume anything — if a field is not visible, set it to null
2. Missing values → null
3. Lists → []
4. Return STRICT JSON ONLY — no markdown fences, no explanation
5. Do NOT add extra fields beyond the schema below

MEDICAL CODE EXTRACTION RULES:
- Extract ALL ICD-10 codes exactly as written (e.g., "I21.0", "J18.9")
- Extract ALL procedure/CPT codes exactly as written
- If codes appear malformed or partial, still extract them as-is — do NOT correct them
- Look for codes in: diagnosis fields, procedure fields, billing line items, any tabular data

INSURANCE COMPANY DETECTION:
- Look for: company name, TPA name, logo text, policy ID prefixes, letterhead
- Look for government scheme markers: "PMJAY", "Ayushman Bharat", "AB-PMJAY", "RSBY", "BPL", state health scheme names
- Look for private TPA markers: "Medi Assist", "FHPL", "Paramount", "Raksha", "Vidal", "Heritage", "MD India"

CLAIM TYPE DETECTION:
- Look for keywords: "reimbursement", "cashless", "pre-authorization", "pre-auth", "enhancement"
- If the form title or header says "Reimbursement Claim Form" → claim_type = "reimbursement"
- If the form title says "Pre-Authorization" or "Cashless" → claim_type = "cashless"



ICD CODE HANDLING:
- Extract ICD-10 codes EXACTLY as written in the document — do not modify them
- If NO ICD code is written in the document, you may SUGGEST one in a separate field "llm_suggested_icd"
- Focus on extracting the EXACT diagnosis name correctly — our deterministic system will verify/assign the final ICD code
- NEVER fabricate an ICD code and present it as if it was in the document

RETURN JSON IN THIS EXACT STRUCTURE:

{
    "document_category": "INSURANCE_FORM",
    "patient_details": {
        "name": null,
        "age": null,
        "gender": null,
        "patient_id": null
    },
    "insurance_details": {
        "insurance_company": null,
        "tpa_name": null,
        "policy_name": null,
        "policy_number": null,
        "scheme_indicators": []
    },
    "hospital_details": {
        "hospital_name": null,
        "hospital_registration_number": null,
        "admission_date": null,
        "discharge_date": null,
        "treating_doctor": null
    },
    "diagnosis_and_procedures": {
        "primary_diagnosis": null,
        "icd_codes": [],
        "procedure_names": [],
        "procedure_codes": []
    },
    "claim_details": {
        "claim_type": null,
        "bill_amount": null,
        "claimed_amount": null,
        "pre_hospitalization_amount": null,
        "post_hospitalization_amount": null,
        "room_charges": null,
        "pharmacy_charges": null,
        "investigation_charges": null,
        "documents_submitted": []
    },
    "bank_details": {
        "account_holder": null,
        "account_number": null,
        "ifsc_code": null,
        "bank_name": null
    },
    "document_metadata": {
        "document_date": null,
        "form_type": null
    },
    "document_summary": ""
}

OCR TEXT:
<<<OCR_TEXT>>>"""


# ── Node 4a: Cross-Document Analyzer (NEW) ───────────────────────────
CROSS_DOC_SYSTEM = (
    "You are an insurance claim cross-verification expert. "
    "You compare the claim form data against supporting documents to find mismatches. "
    "Return strict JSON only."
)

CROSS_DOC_USER = """Compare the claim form data against the supporting documents provided below.

CLAIM FORM STRUCTURED DATA:
<<<STRUCTURED_DATA>>>

SUPPORTING DOCUMENTS (OCR text):
<<<SUPPORTING_DOCS>>>

YOUR TASK: Find every mismatch, inconsistency, or gap between the claim form and the supporting documents.

Check for:
1. **Amount mismatches** — Does the hospital bill total match the claim form's bill_amount? Are there line items in the bill not claimed in the form?
2. **Diagnosis mismatches** — Does the discharge summary or prescription mention a different diagnosis than the claim form?
3. **Treatment mismatches** — Do the prescribed medicines match what's billed in pharmacy charges?
4. **Missing items** — Are there charges in the hospital bill (nursing, ambulance, consumables) that aren't in the claim form?
5. **Date mismatches** — Do admission/discharge dates match across documents?
6. **Doctor/hospital mismatches** — Does the prescribing doctor match the treating doctor on the claim?

Return this JSON:
{
    "cross_doc_findings": [
        {
            "finding_type": "amount_mismatch / diagnosis_mismatch / missing_item / date_mismatch / treatment_mismatch",
            "description": "what was found",
            "claim_form_value": "what the claim form says",
            "supporting_doc_value": "what the other document says",
            "source_doc": "hospital_bill / doctor_prescription / insurance_savings_breakdown",
            "impact": "how this affects the claim"
        }
    ],
    "cross_doc_errors": [
        "bill_claim_amount_mismatch",
        "diagnosis_mismatch_across_docs",
        "charges_in_bill_not_in_claim",
        "prescription_treatment_mismatch"
    ],
    "summary": "1-2 sentence overview of cross-document findings"
}

RULES:
- Only report REAL mismatches you can see in the text
- If documents are consistent, return empty lists
- cross_doc_errors should only contain rule names from this list:
  "bill_claim_amount_mismatch", "diagnosis_mismatch_across_docs",
  "charges_in_bill_not_in_claim", "prescription_treatment_mismatch"
"""


# ── Node 4b: Claim Auditor (Chain of Thought) ────────────────────────
AUDITOR_SYSTEM = (
    "You are an expert Indian Health Insurance Claims Auditor with deep knowledge of "
    "IRDAI regulations, PMJAY/Ayushman Bharat guidelines, and private insurer TPA processes. "
    "You think step-by-step before giving any verdict."
)

AUDITOR_USER = """You must audit the following insurance claim data extracted from a patient's form.
The policy baseline is: **<<<POLICY_BASELINE>>>** (<<<POLICY_TYPE>>>)

<<<CROSS_DOC_SECTION>>>

YOUR TASK: Perform a rigorous line-by-line audit of every field.

## MANDATORY THINKING PROCESS

For EACH field in the data, you MUST reason inside <thinking> tags BEFORE giving a verdict.
Your thinking must follow this EXACT pattern — be thorough, not superficial:

<thinking>
FIELD: [field_name]
VALUE: [value found in form]
EXPECTED: [what this field should contain per policy rules and standard medical/insurance formats]
VALIDATION: [PASS/FAIL] — [specific reason with evidence]
MEDICAL CODE CHECK: [if ICD-10: verify format is LETTER + 2digits + DOT + 1-2digits. "E119" is INVALID (missing dot, should be "E11.9"). "I10" is valid. If not a code: N/A]
BANK VALIDATION: [if IFSC: must be exactly 11 characters (4 letters + 0 + 6 digits). If account holder: compare with patient name. If not bank field: N/A]
DATE VALIDATION: [if date: discharge MUST be after admission. Check for future dates. If not date: N/A]
CLAIM OPTIMIZATION: [Would a senior TPA auditor flag this? How should this field be framed to maximize claim approval?]
SEVERITY: [RED_FLAG / MODERATE_FLAG / CLEAN — with justification]
</thinking>

After thinking through ALL fields, produce your verdict.

## SEVERITY CLASSIFICATION

- **RED_FLAG**: Will cause claim rejection — wrong codes, missing mandatory fields, amount mismatches, invalid policy numbers
- **MODERATE_FLAG**: May delay processing — spelling errors, formatting issues, missing optional fields
- **DO_NOT_TOUCH**: Fields that are correct and complete

## NULL/EMPTY FIELD SEVERITY RULES

- RED_FLAG if missing: primary_diagnosis, ICD codes, insurance_company, claim_type, bill_amount, claimed_amount, admission_date, discharge_date, hospital_name, patient_name, IFSC code
- MODERATE_FLAG if missing: patient_id, hospital_registration_number, treating_doctor, policy_name, gender, document_date
- Diagnosis being blank or null = ALWAYS RED_FLAG (100% rejection)

## CLAIM OPTIMIZATION RULES (SENIOR CONSULTANT EXPERTISE)

### DIAGNOSIS
- hypertension/diabetes as primary → RED (pre-existing). Move to co-morbidities. Primary = acute hospitalization reason.
- Vague diagnosis (fever/weakness/body pain) → RED. Suggest specific ICD-coded diagnosis matching treatment.
- Chronic without "acute exacerbation of" prefix → suggest adding it.

### PRE-EXISTING CONDITIONS
- Check PED waiting period (2-4 yrs). If completed, mention explicitly.
- Never list lifestyle conditions (obesity, smoking) unless medically necessary.
- Multiple conditions → primary = acute/emergency one.

### BILLING
- Room rent > 10% of bill → likely exceeds policy cap → proportional deduction on ENTIRE claim.
- ICU days need clinical justification.
- Consumables (gloves, sutures) → mandatorily covered post-IRDAI 2024 circular. Missing = missed opportunity.
- Pre/post hosp: 30-60 days pre, 60-90 days post. Not claimed = MAJOR missed opportunity.

### DOCUMENTS
- Discharge summary MUST match claim diagnosis. Mismatch = rejection.
- Investigation reports required for every investigation charged.
- Implant → manufacturer sticker + batch number + MRP mandatory.

### TPA RULES
- Medi Assist: Strict docs, 30-day deadline, missing doc = partial rejection.
- Ayushman/PMJAY: Package-based pricing, bill cannot exceed package rate.
- FHPL: Pre-auth required for planned procedures. No pre-auth = 20-30% haircut.
- MD India: Strict coding. Wrong ICD = rejection.

### MISSED CHARGES
- Ambulance (₹2K-5K), nursing, attendant, second opinion → often not claimed.

## ERROR IDENTIFICATION (NO MATH)

You do NOT calculate rupee amounts. Python code does that.

For each error found, add the corresponding rule name to `identified_errors`:

**Full rejection:**
- "primary_diagnosis_missing" — primary_diagnosis is null/empty
- "insurance_company_missing" — insurance_company is null
- "policy_number_missing" — policy_number is null
- "date_inconsistency" — discharge before admission

**Major deduction:**
- "pre_existing_as_primary" — pre-existing condition as primary diagnosis
- "vague_diagnosis" — vague primary diagnosis
- "missing_documents" — investigation reports or prescription not submitted
- "icd_diagnosis_mismatch" — ICD codes don't match primary diagnosis
- "room_rent_exceeds_cap" — room charges > 10% of bill

**Medium deduction:**
- "invalid_icd_format" — ICD code format wrong (e.g., "E119" missing dot)
- "unjustified_icu_days" — ICU charges without clinical context
- "nature_of_illness_unchecked" — illness/injury/maternity not marked
- "branded_drugs_without_justification" — expensive pharmacy without note

**Payment blocks:**
- "ifsc_invalid" — IFSC not 11 characters or wrong format
- "account_name_mismatch" — bank holder ≠ patient name

**Minor:**
- "gender_missing" — gender is null
- "hospital_registration_missing" — hospital registration null

CRITICAL: Only add errors that GENUINELY exist. Do NOT invent errors.

## OUTPUT FORMAT — STRICT JSON:

CRITICAL RULE FOR CORRECTIONS:
Every "correction" field must be SPECIFIC and ACTIONABLE — not generic.

BAD corrections (never do this):
- "Provide primary diagnosis" 
- "Correct the dates"
- "Provide IFSC code"

GOOD corrections (always do this):
- "Change primary diagnosis from blank to the acute condition that caused hospitalization. Check the discharge summary. If the patient had surgery, the surgical condition should be primary (e.g., 'Acute Cholecystitis' with ICD K81.0), NOT any pre-existing condition like hypertension or diabetes."
- "Discharge date 19/01/23 is BEFORE admission date 12/02/23. This is likely a month typo — if patient was admitted on 12 Feb 2023 and stayed ~7 days, discharge should be 19/02/23. Verify with hospital records and correct."
- "IFSC code SBIN002512 is only 10 characters (must be exactly 11). SBI IFSC format is SBIN0XXXXXX. The correct code is likely SBIN0002512 — verify with the bank branch."
- "Gender field is empty. Check the patient's ID proof and fill Male/Female as applicable. Medi Assist requires this field — missing gender causes processing delays."

Think like you're writing instructions for a hospital clerk who needs to fix the form RIGHT NOW.

{
    "thinking_trace": ["<thinking>...</thinking>"],
    "executive_summary": "2-3 sentence overview",
    "red_flags": [
        {
            "field": "field_name",
            "current_value": "what's in the form",
            "issue": "what's wrong",
            "correction": "SPECIFIC step-by-step fix with exact values to write where possible",
            "impact": "what happens if not fixed — include estimated rupee impact"
        }
    ],
    "moderate_flags": [
        {
            "field": "field_name",
            "current_value": "what's in the form",
            "issue": "what's wrong",
            "correction": "SPECIFIC step-by-step fix"
        }
    ],
    "clean_fields": ["list of field names that passed validation"],
    "identified_errors": ["error_code_1", "error_code_2"],
    "medical_code_audit": [
        {
            "code": "code value",
            "type": "ICD-10 / PROCEDURE",
            "status": "VALID / INVALID / SUSPECT",
            "reason": "why"
        }
    ]
}

DATA TO AUDIT:
<<<STRUCTURED_DATA>>>"""


# ── Node 5: Report Generator ─────────────────────────────────────────
REPORT_SYSTEM = (
    "You generate clear, actionable insurance claim audit reports. "
    "Return strict JSON only."
)

REPORT_USER = """Generate a final audit report from the following validated audit results.

POLICY BASELINE: <<<POLICY_BASELINE>>> (<<<POLICY_TYPE>>>)

**FINANCIAL DATA:**
The audit data contains pre-calculated `financial_analysis` with exact rupee amounts computed by Python.
Use those numbers directly. Do NOT recalculate, modify, or round them.

The financial narrative should explain:
- bill_amount = what the patient's total bill is (the target)
- at_risk_amount = how much will be deducted/rejected if form is submitted as-is
- expected_approval_as_is = what patient actually gets without fixes
- recoverable_amount = what they gain by fixing the errors

Example: "Your bill is ₹1,55,000. As submitted, you will likely receive only ₹1,00,000 because of 3 errors causing ₹55,000 in deductions. Fix the errors below to recover the full ₹1,55,000."

## REPORT STRUCTURE — Return this exact JSON:

{
    "executive_summary": "3-4 sentence overview for hospital staff",

    "inconsistencies": {
        "red_flags": [
            {
                "field": "field_name",
                "issue": "description",
                "correction": "specific fix instruction",
                "impact": "rejection risk"
            }
        ],
        "moderate_flags": [
            {
                "field": "field_name",
                "issue": "description",
                "correction": "specific fix instruction"
            }
        ],
        "do_not_touch": ["list of clean fields"]
    },

    "financial_summary": {
        "bill_amount": 0,
        "expected_approval_as_is": 0,
        "at_risk_amount": 0,
        "recoverable_amount": 0,
        "breakdown": "human-readable explanation listing each error and its rupee impact"
    },

    "medical_code_summary": {
        "total_codes_found": 0,
        "valid_codes": 0,
        "invalid_codes": 0,
        "details": "brief narrative"
    },

    CRITICAL: Each suggestion must be specific and actionable — not generic. Include exact values, field names, and step-by-step instructions. The person reading this should know EXACTLY what to write on the form without needing to think.

    "final_suggestions": [
        "Specific actionable step 1",
        "Specific actionable step 2"
    ],

    "bot_message": "Full Markdown message. Structure: Executive Summary → Financial Impact → Red Flags → Moderate Issues → Suggestions"
}

AUDIT DATA:
<<<AUDIT_RESULTS>>>"""

# ── AUTOFILL FLOW: Multi-Doc Extractor ────────────────────────────────
MULTI_DOC_EXTRACT_SYSTEM = (
    "You extract every piece of patient, hospital, insurance, diagnosis, and billing "
    "information from multiple medical documents. Return strict JSON only."
)

MULTI_DOC_EXTRACT_USER = """You are given OCR text from multiple medical documents uploaded by a patient.
These could be: hospital bills, doctor prescriptions, discharge summaries, insurance policies, medical reports.

YOUR TASK: Extract EVERY piece of information that could be used to fill an insurance claim form.

Read ALL documents carefully and extract:

1. **Patient Details** — name, age, gender, date of birth, address, phone, email, patient ID
2. **Insurance Details** — policy number, insurance company, TPA name, sum insured, employee ID, insurer ID card number
3. **Hospital Details** — hospital name, hospital ID, registration number, room type, admission date, discharge date, treating doctor, doctor qualification, doctor registration number
4. **Diagnosis & Procedures** — primary diagnosis, ICD-10 codes, additional diagnoses, co-morbidities, procedure names, ICD-10 PCS codes, line of treatment (medical/surgical/ICU/investigation)
5. **Billing Details** — Extract billing from the AMOUNT column (RATE × QTY), NOT the rate column.
   - CRITICAL: The bill has columns RATE, QTY, AMOUNT IN RS. Always use the AMOUNT column.
   - Extract charges at the SECTION/SUBTOTAL level, not individual line items:
     * If bill groups "ROOM/STAY CHARGE" with subtotal → use that subtotal as one item
     * If bill groups "DOCTOR FEE/PROFESSIONAL CHARGE" with subtotal → use that subtotal
     * If bill groups "INVESTIGATION AND LAB CHARGES" with subtotal → use that subtotal
     * If bill has "TOTAL MEDICINE BILL AMOUNT" → use that as medicine charges
   - billing_summary: a list of section-level charges that ADD UP to total_bill_amount
   - total_bill_amount: the GRAND TOTAL from the bill
   - VALIDATION: the sum of billing_summary amounts MUST equal total_bill_amount. If not, recheck.
6. **Bank Details** — account holder, account number, IFSC code, bank name, PAN
7. **Admission Details** — emergency or planned, hospitalization cause (injury/illness/maternity), if injury: cause, RTA, medico legal, police report, FIR number
8. **Documents Available** — which documents the patient has uploaded (for the checklist)

RULES:
- Extract from ALL documents — cross-reference to get the most complete picture
- If same field appears in multiple docs with different values, note BOTH values
- If a field is not found in ANY document, set it to null
- Do NOT infer or guess — only extract what's explicitly written
- For amounts, extract exact rupee values

DOCUMENT TYPES AND WHAT TO EXTRACT FROM EACH:
 - hospital_bill: patient name, hospital details, admission/discharge dates, billing breakdown, treating doctor
 - doctor_prescription: diagnosis, symptoms, medicines prescribed, treating doctor, clinical findings
 - insurance_policy: insurance company name, TPA name, policy/card number, employee code, corporate name, patient name, gender, age, validity dates, sum insured

IMPORTANT: The insurance_policy document is critical — extract ALL insurance details from it including company name, TPA, card number, and employee details.

INSURANCE CARD MATCHING:
- If the insurance policy document contains MULTIPLE member cards (family floater), match the card to the PATIENT found in other documents (hospital bill / prescription).
- Extract the card number, gender, and age that matches the patient's name, NOT the first card in the document.
- For example, if hospital bill says patient is "POONAM DHAKAD" and insurance PDF has cards for MONU DHAKAR, POONAM DHAKAD, MOHIT DHAKAD — extract POONAM DHAKAD's card details.

DOCUMENTS:
<<<ALL_DOCS>>>

ICD CODE HANDLING:
- Extract ICD-10 codes EXACTLY as written in the documents
- If no ICD code is written, leave the code field as null — our deterministic verification system will assign the correct code
- Focus on getting the DIAGNOSIS NAME exactly right — spell it correctly and completely
- You may add a "llm_suggested_icd" field with your best guess, but it will be verified against our ICD-10 database before use.

RETURN THIS EXACT JSON:

{
    "patient_details": {
        "name": null,
        "gender": null,
        "age": null,
        "date_of_birth": null,
        "address": null,
        "city": null,
        "state": null,
        "pin_code": null,
        "phone": null,
        "email": null,
        "patient_id": null,
        "relationship_to_insured": null,
        "occupation": null
    },
    "insurance_details": {
        "insurance_company": null,
        "tpa_name": null,
        "policy_number": null,
        "certificate_number": null,
        "sum_insured": null,
        "employee_id": null,
        "insurer_id_card": null,
        "currently_other_insurance": null,
        "previously_other_insurance": null
    },
    "hospital_details": {
        "hospital_name": null,
        "hospital_id": null,
        "hospital_registration_number": null,
        "hospital_type": null,
        "admission_date": null,
        "admission_time": null,
        "discharge_date": null,
        "discharge_time": null,
        "room_category": null,
        "treating_doctor": null,
        "doctor_qualification": null,
        "doctor_registration_number": null,
        "doctor_phone": null
    },
    "diagnosis_and_procedures": {
        "primary_diagnosis": null,
        "primary_icd_code": null,
        "additional_diagnosis": null,
        "additional_icd_code": null,
        "co_morbidity_1": null,
        "co_morbidity_2": null,
        "procedure_1": null,
        "procedure_1_icd_pcs": null,
        "procedure_2": null,
        "procedure_3": null,
        "procedure_details": null,
        "line_of_treatment": null,
        "route_of_drug_administration": null,
        "hospitalization_cause": null,
        "injury_cause": null,
        "is_rta": null,
        "is_medico_legal": null,
        "reported_to_police": null,
        "fir_number": null,
        "system_of_medicine": null
    },
    "billing_details": {
        "billing_summary": [
            {"category": "Room & Stay Charges", "amount": 11800},
            {"category": "Doctor / Professional Fees", "amount": 8800},
            {"category": "Investigation & Lab Charges", "amount": 15050},
            {"category": "Medicine / Pharmacy", "amount": 18563}
        ],
        "total_bill_amount": 54213,
        "hospital_bill_subtotal": null,
        "medicine_bill_subtotal": null
    },
    "bank_details": {
        "pan": null,
        "account_number": null,
        "bank_name_branch": null,
        "ifsc_code": null,
        "cheque_dd_payable_to": null
    },
    "admission_details": {
        "type_of_admission": null,
        "expected_days_stay": null,
        "days_in_icu": null,
        "status_at_discharge": null,
        "pre_hospitalization_period_days": null,
        "post_hospitalization_period_days": null,
        "domiciliary_hospitalization": null
    },
    "maternity_details": {
        "date_of_delivery": null,
        "gravida_status": null,
        "expected_delivery_date": null
    },
    "chronic_illness_history": {
        "diabetes": null,
        "heart_disease": null,
        "hypertension": null,
        "hyperlipidemias": null,
        "osteoarthritis": null,
        "asthma_copd": null,
        "cancer": null,
        "alcohol_drug_abuse": null,
        "hiv_std": null,
        "other": null
    },
    "documents_available": [],
    "data_sources": {
        "fields_from_hospital_bill": [],
        "fields_from_prescription": [],
        "fields_from_insurance_doc": [],
        "fields_from_discharge_summary": []
    },
    "extraction_notes": ""
}"""


# ── AUTOFILL FLOW: Claim Form Filler ─────────────────────────────────
FORM_FILLER_SYSTEM = (
    "You are a Medi Assist insurance claim form filling expert. "
    "You map extracted medical data to exact form sections. Return strict JSON only."
)

FORM_FILLER_USER = """You have extracted data from a patient's medical documents.
Your task is to map this data to the Medi Assist Reimbursement Claim Form (Part A, B, and C).

EXTRACTED DATA:
<<<EXTRACTED_DATA>>>

MAP the data to these exact form sections. For each field:
- If data is available → fill it
- If data is NOT available → set to null and add the field name to missing_fields list
- If a field needs optimization for better claim approval → add a suggestion

IMPORTANT CLAIM OPTIMIZATION RULES:

### ICD CODE HANDLING
- Focus on writing the CORRECT and COMPLETE diagnosis name for each field
- You may SUGGEST ICD codes, but our deterministic ICD-10 database will verify and assign the final codes
- Do NOT leave diagnosis names vague — "Dengue Hemorrhagic Fever" is better than "Dengue" or "Fever"
- Primary diagnosis = the ACUTE condition that caused hospitalization (this helps our system match the right ICD code)
- ICD code fields will be filled/verified by our backend system — focus on diagnosis accuracy

### DIAGNOSIS FRAMING
- Primary diagnosis = the ACUTE condition that caused hospitalization
- hypertension/diabetes → NEVER primary. Move to co-morbidities with their ICD codes
- Vague symptoms (fever, weakness, pain) → find the actual disease. "Dengue" not "fever with chills"
- Chronic conditions need "acute exacerbation of" prefix

### SMART SUGGESTIONS GENERATION
Generate 4-8 SPECIFIC, CONTEXTUAL suggestions based on the actual extracted data. Each suggestion must reference actual data from the documents.

Types of suggestions to generate:

1. **DIAGNOSIS OPTIMIZATION** — Based on the actual diagnosis found:
   "Primary diagnosis 'Dengue' should be written as 'Dengue Fever' (complete name). ICD code will be assigned automatically by our verification system. Acute conditions have 95%+ TPA approval rate with proper diagnosis naming."

2. **UNCLAIMED AMOUNTS** — Based on actual bill items and dates:
   "Patient was admitted for 4 days (17/04-21/04). Pre-hospitalization expenses for OPD visits, blood tests, or consultations within 30 days before admission are claimable. Check if patient had any tests before 17/04."
   "Post-hospitalization follow-up visits for 60 days after discharge (until 20/06/26) are claimable. Add any follow-up bills."

3. **BILLING SPECIFIC** — Based on actual charges in the bill:
   "Investigation charges ₹15,050 include Dengue test, Procalcitonin, D-Dimer — these are diagnosis-specific and should get 100% approval."
   "Room charges show ICU (₹3,000) + Private Room (₹8,800). Verify room rent doesn't exceed 1% of sum insured to avoid proportional deduction."

4. **INSURANCE SPECIFIC** — Based on actual insurer/TPA:
   "Royal Sundaram via Vidal Health TPA: Submit within 30 days of discharge. Deadline: 21/05/26."
   "Corporate policy (DAAWAT FOODS) — check if employer has pre-negotiated cashless with this hospital."

5. **DOCUMENT GAPS** — Based on what's missing:
   "Discharge summary not uploaded — this is MANDATORY for reimbursement. Hospital must provide it."
   "Pre-authorization not obtained — for planned admissions, this can cause 20-30% deduction."

6. **IRDAI RULES** — Based on actual treatment:
   "Consumables (gloves, syringes, PPE) used during ICU stay are mandatorily covered per IRDAI 2024 circular. If billed separately, claim them."

BAD suggestions (never generate these):
- "Claim pre-hospitalization and post-hospitalization expenses if applicable" ← too generic
- "Ensure all documents are attached" ← obvious, unhelpful
- "Get ICD code in correct format" ← our system handles this automatically
- "Break down pharmacy charges for better clarity" ← vague

GOOD suggestions (always this specific):
- "Dengue Fever should be primary diagnosis (ICD code will be assigned by our system). Additional diagnosis: Thrombocytopenia if platelet count was low during admission."
- "Procalcitonin test (₹4,000) and D-Dimer (₹2,000) are critical for Dengue diagnosis — these should get 100% approval with proper diagnosis coding."
- "Patient POONAM DHAKAD is covered under spouse's policy (MONU DHAKAR, Employee Code 1211076, DAAWAT FOODS). Relationship to insured should be marked as 'Spouse'."

RETURN THIS JSON:

{
    "section_a_primary_insured": {
        "policy_number": null,
        "certificate_number": null,
        "company_tpa_id": null,
        "name": null,
        "address": null,
        "city": null,
        "state": null,
        "pin_code": null,
        "phone": null,
        "email": null
    },
    "section_b_insurance_history": {
        "currently_other_insurance": null,
        "commencement_date": null,
        "company_name": null,
        "policy_number": null,
        "sum_insured": null,
        "hospitalized_last_4_years": null,
        "diagnosis": null,
        "previously_other_insurance": null
    },
    "section_c_patient_details": {
        "name": null,
        "gender": null,
        "age_years": null,
        "age_months": null,
        "date_of_birth": null,
        "relationship_to_insured": null,
        "occupation": null,
        "address": null,
        "phone": null,
        "email": null
    },
    "section_d_hospitalization": {
        "hospital_name": null,
        "room_category": null,
        "hospitalization_cause": null,
        "date_of_injury_or_disease": null,
        "admission_date": null,
        "admission_time": null,
        "discharge_date": null,
        "discharge_time": null,
        "injury_cause": null,
        "is_rta": null,
        "medico_legal": null,
        "reported_to_police": null,
        "fir_attached": null,
        "system_of_medicine": null
    },
    "section_e_claim_details": {
        "pre_hospitalization_expenses": null,
        "hospitalization_expenses": null,
        "post_hospitalization_expenses": null,
        "health_checkup_cost": null,
        "ambulance_charges": null,
        "other_charges": null,
        "total": null,
        "pre_hospitalization_period_days": null,
        "post_hospitalization_period_days": null,
        "domiciliary_hospitalization": null,
        "hospital_daily_cash": null,
        "surgical_cash": null,
        "critical_illness_benefit": null,
        "convalescence": null,
        "documents_checklist": []
    },
    "section_f_bills_enclosed": [
        {"towards": "Hospital main Bill", "amount": null},
        {"towards": "Pre-hospitalization Bills", "amount": null},
        {"towards": "Post-hospitalization Bills", "amount": null},
        {"towards": "Pharmacy Bills", "amount": null}
    ],
    "section_g_bank_account": {
        "pan": null,
        "account_number": null,
        "bank_name_branch": null,
        "cheque_dd_payable_to": null,
        "ifsc_code": null
    },
    "part_b_hospital_section": {
        "hospital_name": null,
        "hospital_id": null,
        "hospital_type": null,
        "treating_doctor": null,
        "doctor_qualification": null,
        "doctor_registration_number": null,
        "patient_name": null,
        "ip_registration_number": null,
        "gender": null,
        "age": null,
        "date_of_birth": null,
        "admission_date": null,
        "discharge_date": null,
        "type_of_admission": null,
        "status_at_discharge": null,
        "total_claimed_amount": null,
        "primary_diagnosis": null,
        "primary_icd_code": null,
        "additional_diagnosis": null,
        "additional_icd_code": null,
        "co_morbidity_1": null,
        "co_morbidity_1_icd": null,
        "co_morbidity_2": null,
        "co_morbidity_2_icd": null,
        "procedure_1": null,
        "procedure_1_icd_pcs": null,
        "pre_authorization_obtained": null,
        "pre_authorization_number": null
    },
    "part_c_cashless_request": {
        "applicable": false,
        "hospital_name": null,
        "hospital_location": null,
        "hospital_id": null,
        "rohini_id": null,
        "room_rent_per_day": null,
        "investigation_cost": null,
        "icu_charges": null,
        "ot_charges": null,
        "surgeon_anesthesia_fees": null,
        "medicines_consumables": null,
        "other_expenses": null,
        "package_charges": null,
        "total_expected_cost": null
    },
    "missing_fields": [
        {"field": "field_name", "section": "which section", "how_to_get": "upload policy document / ask hospital / check insurance card"}
    ],
    "optimization_suggestions": [
        "Specific suggestion for better claim approval"
    ],
    "claimable_summary": {
        "total_bill": null,
        "estimated_claimable": null,
        "items_not_claimable": [],
        "tip": "Expert tip for maximizing claim"
    }
}"""

