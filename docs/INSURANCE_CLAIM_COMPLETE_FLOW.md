# Insurance claim pipeline — complete flow & models

This document describes the **production** insurance flow used by `POST /medilocker/insurance/analyze` and `/insurance/analyze/stream` in MediLocker Lambda: the **LangGraph** pipeline in `claim_validator_graph.py` (it supersedes the older monolithic path in `insurance_extraction_service.py` for these routes).

---

## Models & external systems

| Layer | Component | Role |
|--------|-----------|------|
| **API** | FastAPI `medilocker_router` | Multipart upload; calls `validate_claim` / `validate_claim_streaming` |
| **Orchestration** | **LangGraph** `StateGraph` | Compiles graph: `build_claim_validator_graph()` → singleton `get_claim_validator()` |
| **State schema** | `ClaimValidationState` (`TypedDict`) | Carries inputs, per-node outputs, `timings`, `error` |
| **OCR** | **AWS Textract** | `extract_text_from_image` (bytes) / `extract_text_from_pdf_s3` (PDF via temp S3 key) |
| **Object storage** | **Amazon S3** | Temp key `{S3_FOLDER_PREFIX}_temp/insurance/{temp_id}/document.{ext}` for PDF OCR; deleted after Textract |
| **LLM** | **Groq** OpenAI-compatible API | `OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL)` |
| **LLM model id** | Env `CLAIM_VALIDATOR_MODEL` | Default: `meta-llama/llama-4-scout-17b-16e-instruct` (`app/config.py`) |
| **Policy routing** | `detect_policy_baseline()` in `policy_router.py` | **No LLM** — rules over `insurance_details` (company, TPA, scheme indicators) vs `GOVT_SCHEME_MARKERS`, `PRIVATE_TPA_MARKERS`, `PRIVATE_INSURER_NAMES`; fallbacks `DEFAULT_GOVT_POLICY` (`ayushman_bharat`), `DEFAULT_PRIVATE_POLICY` (`medi_assist`) |
| **Prompts** | `claim_prompts.py` | `EXTRACTION_*`, `AUDITOR_*`, `REPORT_*` — define JSON shapes for nodes 2, 4, 5 |

---

## Step-by-step pipeline (ordered)

1. **Input** — `file_bytes`, `filename` (allowed: images per `ALLOWED_EXTENSIONS`, plus `pdf`).
2. **Node `ocr_extractor`** — If unsupported extension → `error`, stop. If **PDF**: upload to temp S3 → `extract_text_from_pdf_s3` → delete object. If **image**: `extract_text_from_image`. Empty OCR → `error`, stop.
3. **Node `claim_field_extractor`** — LLM with `EXTRACTION_SYSTEM` / `EXTRACTION_USER` + OCR text, `response_format=json_object` → `structured_data` (+ `source_filename`). Parse failure → `error`, stop.
4. **Node `policy_router`** — `detect_policy_baseline(structured_data)` → `policy_baseline`, `policy_type` (`government` | `private`).
5. **Node `claim_auditor`** — LLM with `AUDITOR_SYSTEM` / `AUDITOR_USER` (injects policy + JSON of `structured_data`) → JSON with `thinking_trace`, `red_flags`, `moderate_flags`, `financial_analysis`, `medical_code_audit`, etc. Failure → `error`, stop.
6. **Node `report_generator`** — LLM with `REPORT_SYSTEM` / `REPORT_USER` (injects `audit_results`) → `final_report` (executive summary, inconsistencies, financial summary, `bot_message`, …).
7. **Output** — `validate_claim` returns: `structured_data`, `policy_baseline`, `policy_type`, `thinking_trace`, `audit_results`, `final_report`, `error`, `timings` (no `file_bytes`).

**Streaming:** `validate_claim_streaming` uses `graph.astream(..., stream_mode="updates")` and yields per-node payloads plus a final `__done__` event.

---

## Mermaid: full graph (nodes, gates, models)

```mermaid
flowchart TB
    subgraph API["HTTP"]
        H["POST /medilocker/insurance/analyze"]
        HS["POST /medilocker/insurance/analyze/stream"]
        H --> V["validate_claim()"]
        HS --> VS["validate_claim_streaming()"]
    end

    subgraph Graph["LangGraph — get_claim_validator()"]
        START([START]) --> N1["1. ocr_extractor"]

        N1 --> G1{ocr_text?}
        G1 -->|no| END1([END])
        G1 -->|yes| N2["2. claim_field_extractor"]

        N2 --> G2{structured_data?}
        G2 -->|no| END2([END])
        G2 -->|yes| N3["3. policy_router"]

        N3 --> N4["4. claim_auditor"]

        N4 --> G3{audit_results?}
        G3 -->|no| END3([END])
        G3 -->|yes| N5["5. report_generator"]

        N5 --> END4([END])
    end

    V --> Graph
    VS --> Graph

    subgraph N1detail["Node 1 — OCR (no LLM)"]
        T1["AWS Textract"]
        S3temp["S3 temp upload + delete — PDF only"]
        N1 -.-> T1
        N1 -.-> S3temp
    end

    subgraph N2detail["Node 2 — Extraction (LLM)"]
        M2["CLAIM_VALIDATOR_MODEL via Groq"]
        P2["EXTRACTION_SYSTEM / EXTRACTION_USER"]
    end

    subgraph N3detail["Node 3 — Policy router (no LLM)"]
        PR["detect_policy_baseline()"]
        DEF["Defaults: ayushman_bharat / medi_assist"]
    end

    subgraph N4detail["Node 4 — Auditor (LLM)"]
        M4["CLAIM_VALIDATOR_MODEL via Groq"]
        P4["AUDITOR_SYSTEM / AUDITOR_USER"]
    end

    subgraph N5detail["Node 5 — Report (LLM)"]
        M5["CLAIM_VALIDATOR_MODEL via Groq"]
        P5["REPORT_SYSTEM / REPORT_USER"]
    end

    N2 --- N2detail
    N3 --- N3detail
    N4 --- N4detail
    N5 --- N5detail
```

---

## Mermaid: state (`ClaimValidationState`) through the run

```mermaid
flowchart LR
    subgraph In["Input"]
        FB["file_bytes"]
        FN["filename"]
    end

    subgraph S1["After OCR"]
        OT["ocr_text"]
    end

    subgraph S2["After extraction"]
        SD["structured_data\n(+ source_filename)"]
    end

    subgraph S3["After policy router"]
        PB["policy_baseline"]
        PT["policy_type"]
    end

    subgraph S4["After auditor"]
        TT["thinking_trace"]
        AR["audit_results"]
    end

    subgraph S5["After report"]
        FR["final_report"]
    end

    subgraph Meta["Meta"]
        ER["error"]
        TM["timings per stage"]
    end

    In --> S1 --> S2 --> S3 --> S4 --> S5
    S5 --> Out["API JSON response"]
    Meta --> Out
```

---

## Mermaid: extraction JSON shape (Node 2 — high level)

The LLM must return a single JSON object. Top-level keys enforced by prompts include:

```mermaid
mindmap
  root((structured_data))
    document_category
    primary_insured_details
    insurance_history
    patient_details
    hospitalization_details
    claim_details
    bill_details
    documents_submitted
    bank_details
    insurance_details
    diagnosis_and_procedures
    document_metadata
    document_summary
```

Full field lists are in `app/services/prompts/claim_prompts.py` inside `EXTRACTION_USER` (nested objects such as `cash_benefits`, `bill_details[]`, `insurance_details.scheme_indicators[]`, etc.).

---

## Mermaid: auditor output (Node 4) and report output (Node 5)

```mermaid
flowchart TB
    subgraph Audit["audit_results — LLM JSON"]
        A1["thinking_trace"]
        A2["executive_summary"]
        A3["red_flags / moderate_flags / clean_fields"]
        A4["financial_analysis"]
        A5["medical_code_audit"]
    end

    subgraph Final["final_report — LLM JSON"]
        F1["executive_summary"]
        F2["inconsistencies"]
        F3["financial_summary"]
        F4["medical_code_summary"]
        F5["final_suggestions"]
        F6["bot_message — Markdown"]
    end

    Audit --> Final
```

---

## Policy router logic (conceptual)

```mermaid
flowchart TD
    I["structured_data.insurance_details"] --> C{company_name matches\ngovt scheme?}
    C -->|yes| G["policy_baseline = DEFAULT_GOVT_POLICY\npolicy_type = government"]
    C -->|no| P["policy_baseline = slug\npolicy_type = private"]
    I --> T{tpa_name matches\nprivate TPA list?}
    T -->|yes| MP["policy_baseline = DEFAULT_PRIVATE_POLICY\npolicy_type = private"]
    I --> S{scheme_indicators}
    S -->|govt marker| G
    S -->|private TPA marker| MP
    S -->|none| F["default govt baseline"]
```

---

## Code references

- Graph & state: `backend/medilocker_lambda/app/services/claim_validator_graph.py`
- Router endpoints: `backend/medilocker_lambda/app/routers/medilocker_router.py` (`/insurance/analyze`, `/insurance/analyze/stream`)
- Prompts & JSON contracts: `backend/medilocker_lambda/app/services/prompts/claim_prompts.py`
- Policy detection: `backend/medilocker_lambda/app/services/policy_router.py`
- Config: `backend/medilocker_lambda/app/config.py` — `CLAIM_VALIDATOR_MODEL`, `GROQ_*`, `DEFAULT_*_POLICY`

---

## Legacy note

`insurance_extraction_service.extract_insurance_data_from_file` (OCR → `extract_insurance_structured_data_from_text` → `analyze_insurance_claim`) remains in the codebase but **the analyze routes above use `validate_claim` from `claim_validator_graph`**, not that monolithic function.
