# Backend Handover

> **Written:** 5 August 2026
> **Outgoing maintainer:** Nitesh Kothari
> **Scope:** the `backend` repository (all `*_lambda` services, SAM infrastructure, ops scripts)

Everything else — architecture, per-service flows, API bodies — is indexed in
[README.md](README.md).

---

## 1. Do these first (in order)

| #   | Action                                       | Why it is urgent                                                                                            |
| --- | -------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| 1   | **Take custody of the real `template.yaml`** | It is _not_ in git (see §2). Without it you cannot deploy. Get it from the outgoing maintainer out-of-band. |

---

## 2. External accounts needing an owner

Each of these can break production and none should be tied to a departing
person's login.

| Service                                                        | Used for                                                                      | Hand over                                                                                          |
| -------------------------------------------------------------- | ----------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| **AWS** (account hosting stack `sam-app`, region `ap-south-1`) | All infrastructure                                                            | Root/admin access, billing, IAM users                                                              |
| **Razorpay**                                                   | Payments, payment links, webhooks                                             | Dashboard access; note this is a **live** merchant account                                         |
| **OpenAI**                                                     | `chat_lambda`                                                                 | Org access + billing                                                                               |
| **Groq**                                                       | `chat_lambda`, `medilocker_lambda`                                            | Console access                                                                                     |
| **Brevo**                                                      | All transactional email (OTP, verification, password reset, payment receipts) | Account + SMTP credentials                                                                         |
| **ABDM / NHA**                                                 | ABHA + health-record exchange                                                 | Sandbox `SBXID_023051`; portal login, and the production onboarding application if started         |
| **Google Cloud** (`kokoro-doctor-meet-project`)                | Meet/Jitsi spike                                                              | Project ownership — see §3.2                                                                       |
| **AWS SNS**                                                    | SMS OTP delivery                                                              | Within the AWS account; check the spending limit and whether the account is out of the SMS sandbox |
| **Domain `kokoro.doctor`**                                     | Frontend + API                                                                | Registrar and DNS access                                                                           |
| **Mixpanel**                                                   | Product analytics (see `MIXPANEL_BUTTON_CTA_TRACKING.md`)                     | Project access                                                                                     |

Also confirm who owns the **RAG server** at `http://13.203.1.165:8000/rag` — it is
a hardcoded IP on plain HTTP that `chat_lambda` depends on. It is not defined in
this SAM template, so it is deployed and managed somewhere else. If nobody claims
it, chat degrades when it goes down.

---

## 3. Repository map

```
backend/
├── template.example.yaml   # Sanitised infra — deploy from this (real one is out-of-band)
├── samconfig.toml          # SAM deploy defaults: stack sam-app, region ap-south-1
├── readme.md               # Project overview
├── docs/                   # All documentation — start at docs/README.md
├── scripts/                # Operational scripts (see SCRIPTS.md)
├── ocr_layer/              # Shared Lambda layer: Textract wrapper (package ocr_core)
├── tools/fidelius-cli/     # ABDM FHIR encryption reference implementation
├── meet-test/              # Google Meet / Jitsi spike — unused, see §3.2
│
├── auth_lambda/            # Signup, login, OTP, JWT issuance, admin delete
├── userService_lambda/     # User profile reads
├── doctorsService_lambda/  # Doctor profiles, documents, availability slots
├── booking_lambda/         # Appointments + subscription plans
├── payment_lambda/         # Razorpay links, webhooks, earnings ledger
├── doctor_payouts_lambda/  # Doctor earnings summaries and payouts
├── hospitals_lambda/       # Hospital accounts, staff workflows, relations
├── medilocker_lambda/      # Medical records, OCR, prescriptions, insurance claims
├── ocr_worker_lambda/      # SQS-triggered async OCR worker
├── chat_lambda/            # AI chat with RAG fallback
└── abha_lambda/            # ABDM / ABHA integration (HIP + HIU)
```

Every service follows the same shape: `app/main.py` (FastAPI + Mangum handler),
`config.py`, `logger.py`, `routers/`, `services/`, `utils/`, `models/`.

---

## 4. Where the remaining knowledge lives

- **This repo:** [docs/README.md](README.md) indexes ~45 documents.
- **Notion:** ABDM specifications live under the parent page _"ABDM ABHA V3 APIs"_
  — Milestone 1/2/3 pages plus the _"ABDM / WASA Compliance Audit"_ (component
  level, dated 2 June 2026). These are the canonical ABDM references and are
  **not** mirrored into this repo. Make sure the team has Notion access before
  the handover completes.
- **Out-of-band:** the real `template.yaml`, and credentials for the accounts in §4.

---
