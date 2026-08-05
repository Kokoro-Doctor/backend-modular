# Kokoro Doctor — backend documentation

Everything documented about this backend, indexed. If you are new, start with
[ONBOARDING.md](ONBOARDING.md). If you are taking the project over, start with
[HANDOVER.md](HANDOVER.md).

---

## Start here

| Document | What it covers |
|---|---|
| **[HANDOVER.md](HANDOVER.md)** | **Read first if you are inheriting this project.** What is not in git, which credentials to rotate, external account owners, known risks |
| [ONBOARDING.md](ONBOARDING.md) | Day one: prerequisites, repo layout, running a service locally, conventions |
| [HIGH_LEVEL_DESIGN.md](HIGH_LEVEL_DESIGN.md) | System architecture end to end |
| [APPLICATION_FLOW_DIAGRAM.md](APPLICATION_FLOW_DIAGRAM.md) | How a request moves through the system |

## Building and running

| Document | What it covers |
|---|---|
| [DEPLOYMENT.md](DEPLOYMENT.md) | SAM build and deploy, per-function config, the OCR layer, rollback, pitfalls |
| [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md) | Every env var by service; which are secrets; which must match across services |
| [OPERATIONS_RUNBOOK.md](OPERATIONS_RUNBOOK.md) | Reading logs, triage by symptom, common inspections |
| [SCRIPTS.md](SCRIPTS.md) | The five operational scripts in `scripts/` and their risks |

## API reference

| Document | What it covers |
|---|---|
| [LAMBDA_FUNCTIONS.md](LAMBDA_FUNCTIONS.md) | All eleven services and every route, verified against the code |
| [docs_api/](docs_api/README.md) | Request/response examples per service, with a changelog |
| [API_TEST_BODIES.md](API_TEST_BODIES.md) | Pointer to the above |

Per-service API files: [auth](docs_api/auth-lambda.md) ·
[user](docs_api/user-service-lambda.md) · [doctors](docs_api/doctors-service-lambda.md) ·
[booking](docs_api/booking-lambda.md) · [payment](docs_api/payment-lambda.md) ·
[payouts](docs_api/doctor-payouts-lambda.md) · [hospitals](docs_api/hospitals-lambda.md) ·
[medilocker](docs_api/medilocker-lambda.md) · [chat](docs_api/chat-lambda.md) ·
[abha](docs_api/abha-lambda.md)

## Data model

| Document | What it covers |
|---|---|
| [DATABASE_TABLES.md](DATABASE_TABLES.md) | All 26 DynamoDB tables — attributes, keys, GSIs |
| [CURRENT_USER_DOCTOR_RELATIONSHIP.md](CURRENT_USER_DOCTOR_RELATIONSHIP.md) | How patient–doctor links are modelled today |
| [DR_USER_CONNECTION_FLOW.md](DR_USER_CONNECTION_FLOW.md) | How a patient becomes connected to a doctor |
| [ADD_HOSPITAL_PORTAL_DOCTOR_CONNECTION.md](ADD_HOSPITAL_PORTAL_DOCTOR_CONNECTION.md) | Hospital portal doctor connections |

## Feature areas

### Authentication

- [AUTH_FLOW_DETAILED.md](AUTH_FLOW_DETAILED.md) — signup, login, OTP, JWT
- [../auth_lambda/docs/AUTH_FLOW_DOCUMENTATION.md](../auth_lambda/docs/AUTH_FLOW_DOCUMENTATION.md) — service-level detail
- [../auth_lambda/docs/AUTH_FOLDER_STRUCTURE.md](../auth_lambda/docs/AUTH_FOLDER_STRUCTURE.md)

### Medical records and documents

- [../medilocker_lambda/docs/MEDILOCKER_FLOW.md](../medilocker_lambda/docs/MEDILOCKER_FLOW.md)
- [../medilocker_lambda/docs/MEDILOCKER_STRUCTURE.md](../medilocker_lambda/docs/MEDILOCKER_STRUCTURE.md)
- [../medilocker_lambda/docs/SERVICES_REFERENCE.md](../medilocker_lambda/docs/SERVICES_REFERENCE.md)
- [../medilocker_lambda/docs/FETCH_FILES_ENDPOINT.md](../medilocker_lambda/docs/FETCH_FILES_ENDPOINT.md)
- [DOCUMENT_UPLOAD_AND_ACCESS.md](DOCUMENT_UPLOAD_AND_ACCESS.md)
- [FILE_UPLOAD_METHODS.md](FILE_UPLOAD_METHODS.md)
- [HOSPITAL_IMPORT_SHARED_DRIVE.md](HOSPITAL_IMPORT_SHARED_DRIVE.md)

### OCR

- [OCR_IMPLEMENTATION.md](OCR_IMPLEMENTATION.md)
- [AWS_TEXTRACT_USAGE.md](AWS_TEXTRACT_USAGE.md)

### Prescriptions and clinical AI

- [PRESCRIPTION_AND_CASE_ANALYSIS_SYSTEM.md](PRESCRIPTION_AND_CASE_ANALYSIS_SYSTEM.md)
- [PRESCRIPTION_FLOW_IMPROVEMENTS.md](PRESCRIPTION_FLOW_IMPROVEMENTS.md)
- [../medilocker_lambda/docs/PRESCRIPTION_GENERATION.md](../medilocker_lambda/docs/PRESCRIPTION_GENERATION.md)
- [../medilocker_lambda/docs/CLINICAL_QUERY.md](../medilocker_lambda/docs/CLINICAL_QUERY.md)
- [../medilocker_lambda/docs/CLINICAL_QUERY_HISTORY.md](../medilocker_lambda/docs/CLINICAL_QUERY_HISTORY.md)

### Insurance and claims

- [INSURANCE_CLAIM_COMPLETE_FLOW.md](INSURANCE_CLAIM_COMPLETE_FLOW.md)
- [INSURANCE_FORM_PROCESS.md](INSURANCE_FORM_PROCESS.md)
- [INSURANCE_CLAIM_PDF_DOWNLOAD.md](INSURANCE_CLAIM_PDF_DOWNLOAD.md)
- [HOSPITAL_INSURANCE_PRESCRIPTION_FLOWS.md](HOSPITAL_INSURANCE_PRESCRIPTION_FLOWS.md)
- [PREAUTH_SERVICE_ARCHITECTURE.md](PREAUTH_SERVICE_ARCHITECTURE.md)

### Payments

- [PAYMENT_SYSTEM_ANALYSIS.md](PAYMENT_SYSTEM_ANALYSIS.md)

### Chat

- [../chat_lambda/CHAT_LAMBDA_FLOW.md](../chat_lambda/CHAT_LAMBDA_FLOW.md)
- [LANGGRAPH_FASTAPI.md](LANGGRAPH_FASTAPI.md)

### ABDM / ABHA

- **[ABDM_INTEGRATION.md](ABDM_INTEGRATION.md)** — milestone status, blockers, code map. Read this first
- [ABDM_M2_FLOW_DIAGRAM.md](ABDM_M2_FLOW_DIAGRAM.md) — consent and data-flow diagram
- [docs_api/abha-lambda.md](docs_api/abha-lambda.md) — the most detailed API reference in the repo

> The canonical ABDM specifications live in **Notion**, not here — see
> [ABDM_INTEGRATION.md](ABDM_INTEGRATION.md) §6.

### Analytics

- [MIXPANEL_BUTTON_CTA_TRACKING.md](MIXPANEL_BUTTON_CTA_TRACKING.md)

## Planning

- [INTERN_PROJECT_IDEAS.md](INTERN_PROJECT_IDEAS.md) — candidate projects, not system documentation

---

## Diagrams

`langgraph_preauth_mediclaim_hld.svg` and `langgraph_preauth_mediclaim_lld.svg`
(with `.png` versions) accompany
[PREAUTH_SERVICE_ARCHITECTURE.md](PREAUTH_SERVICE_ARCHITECTURE.md).

---

## Keeping this current

- Change an endpoint → update [LAMBDA_FUNCTIONS.md](LAMBDA_FUNCTIONS.md) and the
  matching file in [docs_api/](docs_api/README.md), including its changelog.
- Add a table → update [DATABASE_TABLES.md](DATABASE_TABLES.md).
- Add an env var → update [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md)
  **and** `template.example.yaml`, or the committed template drifts out of sync
  and stops being deployable.
- Add a document → add it to this index.

Service docs living beside their code (`auth_lambda/docs/`,
`medilocker_lambda/docs/`, `chat_lambda/`) are linked above. They were gitignored
until August 2026 — if a `*.md` file you create under a service directory seems
to vanish from `git status`, check that service's `.gitignore`.
