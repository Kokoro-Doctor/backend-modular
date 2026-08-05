# Hospital, insurance, and prescription flows (Mermaid)

This document ties together **patient ↔ hospital booking**, **hospital patient-document uploads**, **insurance claim analysis**, and **prescription generation** as implemented in Kokoro Doctor. Use any diagram block in Mermaid-compatible viewers (GitHub, Notion, VS Code preview).

---

## 1. End-to-end overview (all roles + AI pipelines)

```mermaid
flowchart TB
    subgraph Patient["Patient"]
        P1[Welcome / PatientAppNavigation]
        P1 --> P2[UserDashboard]
        P1 --> P3[Medilocker: upload documents]
        P1 --> P4[Hospitals: discover & book]
        P4 --> P5[Hospital booking & payment]
        P3 --> S3A[(S3 + MedilockerDocuments)]
        P3 --> OCR[Textract OCR → Groq extraction]
        OCR --> S3A
    end

    subgraph Doctor["Doctor"]
        D1[DoctorAppNavigation]
        D1 --> D2[GeneratePrescription / Prescription.jsx]
        D2 --> API1["POST /medilocker/users/{user_id}/prescription"]
        D2 --> API2[POST /medilocker/prescription]
        D2 --> SAVE["POST /medilocker/users/{user_id}/prescription/save"]
        API1 --> GROQ[Groq synthesis]
        API2 --> TEX1[Textract + per-doc extraction + synthesis]
        SAVE --> S3A
    end

    subgraph HospitalStaff["Hospital staff portal"]
        H1[Staff app: add/update patient]
        H1 --> H2["POST /hospitals/staff/add-patient or /update_patient (JWT)"]
        H2 --> H3[S3: Medilocker/Users/+patient_id+/]
        H2 --> H4[(MedilockerDocuments: source=HOSPITAL, hospital_id)]
        H2 --> OCR2[Textract OCR — async via SQS]
        OCR2 --> H4
    end

    subgraph InsuranceUI["Insurance (hospital / staff UI)"]
        I1[Upload claim document]
        I1 --> I2["POST /medilocker/insurance/analyze"]
        I2 --> I3[OCR + structured extract + claim validation graph]
        I3 --> I4[Editable form + PDF download]
    end

    Patient -.->|subscribes / books doctors| Doctor
```

---

## 2. Hospital: patient document upload (unified with Medilocker)

> The old standalone `/hospital/upload`, `/hospital/presign-upload`,
> `/hospital/confirm-upload` endpoints (medilocker_lambda, API-key auth,
> storage-only into a separate `HospitalFiles` table) have been **removed**.
> Hospital uploads now go through the JWT-secured staff-app routes and land in
> the same `MedilockerDocuments` table patients use, tagged `source=HOSPITAL`.
> See `docs/DOCUMENT_UPLOAD_AND_ACCESS.md` for the full model.

```mermaid
flowchart TD
    A[Hospital staff app] --> B[JWT: Authorization: Bearer ...]
    B --> C{hospital_id matches token?}
    C -->|No| Z[403]
    C -->|Yes| D["POST /hospitals/staff/add-patient or /update_patient"]

    D --> E["S3: Medilocker/Users/{patient_id}/{file_id}/original.{ext}"]
    D --> F[(MedilockerDocuments: source=HOSPITAL, hospital_id)]
    F --> G[SQS OCR queue]
    G --> H[OCRWorkerLambda: Textract]
    H --> F
```

**Access:** `GET /hospitals/staff/patients/{user_id}/documents` returns the
patient's own docs + this hospital's docs only (never another hospital's).

---

## 3. Insurance: analyze claim document (stateless API)

```mermaid
flowchart LR
    A[Multipart: insurance PDF or image] --> B["POST /medilocker/insurance/analyze"]
    B --> C[claim_validator_graph: validate_claim]
    C --> D[Structured fields + analysis payload]
    D --> E[UI: HospitalInsuranceClaim → MediAssistFormA]
    E --> F[HTML/PDF claim form]

    B2["POST /medilocker/insurance/analyze/stream"] --> C2[SSE: streaming node updates]
    C2 --> D
```

Core pipeline (conceptual): **OCR → structured extraction → claim analysis / validation** (see `insurance_extraction_service` and `claim_validator_graph` in MediLocker Lambda).

---

## 4. Prescription: two paths (stored Medilocker vs ad-hoc upload)

```mermaid
flowchart TD
    subgraph FlowA["Flow A — from patient Medilocker (primary)"]
        A1["POST /medilocker/users/{user_id}/prescription"]
        A2[Load latest documents from DynamoDB]
        A3[Filter: ocr + structured COMPLETED]
        A4[build_patient_context → single Groq synthesis]
        A1 --> A2 --> A3 --> A4
    end

    subgraph FlowB["Flow B — doctor uploads files (no Medilocker storage)"]
        B1[POST /medilocker/prescription]
        B2[Parallel Textract on files]
        B3[Parallel extraction per document]
        B4[Merge structured data → one synthesis]
        B1 --> B2 --> B3 --> B4
    end

    subgraph Save["Persist to patient locker"]
        S1["POST /medilocker/users/{user_id}/prescription/save"]
        S2[Prescription PDF → S3 as Medilocker document]
        S1 --> S2
    end

    A4 --> OUT[Prescription text + optional patient_details]
    B4 --> OUT
    OUT -.-> Save
```

**Related:** `POST /medilocker/users/{user_id}/clinical-query` reuses the same **processed document context** for Q&A (not shown above).

---

## 5. Patient: hospital discovery and booking (app navigation)

```mermaid
flowchart LR
    P[PatientAppNavigation → Hospitals] --> L[AllHospitals]
    L --> R[HospitalsInfoWithRating]
    R --> B[HospitalBookingNext]
    B --> PAY[HospitalPaymentApp]
```

This path is **separate** from hospital API-key upload and from Medilocker clinical AI flows; it is the **consumer booking** journey.

---

## 6. Discharge summary analyze (same family as insurance; stateless)

```mermaid
flowchart LR
    X[Discharge PDF or image] --> Y["POST /medilocker/discharge/analyze"]
    Y --> Z[OCR + discharge extraction + analysis]
```

---

## Quick reference

| Area | Main entry | Persistence |
|------|------------|-------------|
| Hospital patient docs | `/hospitals/staff/add-patient`, `/update_patient` (JWT) | `MedilockerDocuments` (source=HOSPITAL) |
| Insurance analyze | `/medilocker/insurance/analyze` | Stateless (optional UI saves elsewhere) |
| Prescription from locker | `/medilocker/users/{user_id}/prescription` | Reads `MedilockerDocuments` |
| Prescription from uploads | `/medilocker/prescription` | Stateless |
| Save prescription PDF | `/medilocker/users/{user_id}/prescription/save` | Writes to Medilocker |

For broader app routing and tables, see `docs/APPLICATION_FLOW_DIAGRAM.md` and `docs/PRESCRIPTION_AND_CASE_ANALYSIS_SYSTEM.md`.
