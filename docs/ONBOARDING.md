# Onboarding — first day on the backend

Goal: get a service running locally, understand the shape of the codebase, and
know where to look next. Budget about an hour.

If you are picking this project up after the previous maintainer left, read
[HANDOVER.md](HANDOVER.md) **first** — some things you need are not in git.

---

## 1. What this is

A serverless healthcare backend on AWS. Eleven independent FastAPI applications,
each packaged as a Lambda function behind one API Gateway, sharing DynamoDB
tables and one S3 bucket.

- **Runtime:** Python 3.11, FastAPI, [Mangum](https://mangum.io) (ASGI → Lambda adapter)
- **Infra:** AWS SAM (`template.example.yaml`), region `ap-south-1`, stack `sam-app`
- **Data:** DynamoDB (26 tables), S3 (`kokoro-doctor`), SQS (OCR queue)
- **Frontend:** React Native / Expo at `https://kokoro.doctor` (separate repo)

Architecture detail: [HIGH_LEVEL_DESIGN.md](HIGH_LEVEL_DESIGN.md) and
[APPLICATION_FLOW_DIAGRAM.md](APPLICATION_FLOW_DIAGRAM.md).

---

## 2. Prerequisites

| Tool | Version | Check |
|---|---|---|
| Python | 3.11 (match the Lambda runtime) | `python3 --version` |
| AWS SAM CLI | ≥ 1.136 | `sam --version` |
| AWS CLI | v2, configured for `ap-south-1` | `aws sts get-caller-identity` |
| Docker | any recent | needed for `sam local` and container builds |

```bash
brew install aws-sam-cli awscli
```

You also need AWS credentials with access to the account hosting `sam-app`. Ask
whoever took ownership per [HANDOVER.md](HANDOVER.md) §4.

---

## 3. Repository layout

Every service is a directory ending in `_lambda`, and they all have the same
internal shape:

```
<service>_lambda/
├── requirements.txt
└── app/
    ├── main.py       # FastAPI app + `handler = Mangum(app)` — the Lambda entrypoint
    ├── config.py     # reads environment variables
    ├── logger.py     # structured logging
    ├── routers/      # HTTP routes (thin — parse, validate, delegate)
    ├── services/     # business logic (this is where the real work is)
    ├── models/       # Pydantic request/response schemas
    └── utils/        # helpers: db, jwt, email, sms, errors
```

The one exception is `ocr_worker_lambda`, which is SQS-triggered rather than
HTTP-triggered, so its entrypoint is `app/handler.py` and it has no routers.

**Reading order for a new endpoint:** `routers/` → `services/` → `utils/`.

| Service | Base path | What it owns |
|---|---|---|
| `auth_lambda` | `/auth` | Signup, login, OTP, JWT issuance, admin account deletion |
| `userService_lambda` | `/users` | User profile reads |
| `doctorsService_lambda` | `/doctorsService` | Doctor profiles, documents, availability slots |
| `booking_lambda` | `/booking` | Appointments, subscription plans, user subscriptions |
| `payment_lambda` | `/process-payment` | Razorpay links and webhooks, earnings ledger |
| `doctor_payouts_lambda` | `/payouts` | Doctor earnings and payouts |
| `hospitals_lambda` | `/hospitals` | Hospital accounts, staff workflows, patient-doctor relations |
| `medilocker_lambda` | `/medilocker`, `/hospital` | Medical records, OCR, prescriptions, insurance claims |
| `ocr_worker_lambda` | *(SQS)* | Async OCR processing |
| `chat_lambda` | `/chat` | AI chat with RAG fallback |
| `abha_lambda` | `/abha`, `/api/v3` | ABDM / ABHA integration |

Full endpoint list: [LAMBDA_FUNCTIONS.md](LAMBDA_FUNCTIONS.md).
Request/response examples: [docs_api/](docs_api/README.md).

---

## 4. Run a service locally

Each service has its own virtualenv and its own `.env` (both gitignored — `.env`
files are *not* in the repo, so you will need values from
[ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md)).

```bash
cd auth_lambda
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `auth_lambda/.env` with the variables that service needs, then run it as
an ordinary FastAPI app:

```bash
uvicorn app.main:app --reload --port 8000
```

Interactive API docs are then at `http://localhost:8000/docs` — FastAPI
generates them from the routers, which is the fastest way to explore a service.

> Local runs talk to **real AWS DynamoDB and S3** using your AWS credentials.
> There is no local DynamoDB configured. Be careful: there is only one
> environment, and it is production (see [HANDOVER.md](HANDOVER.md) §5).

### Via SAM instead

To exercise the actual Lambda packaging and API Gateway routing:

```bash
sam build
sam local start-api --port 3000
```

Slower, but it catches packaging problems (missing dependencies, layer import
paths) that `uvicorn` will not.

---

## 5. Deploying

Do not deploy on day one. When you are ready, read
[DEPLOYMENT.md](DEPLOYMENT.md) — in particular the fact that secrets are now
passed as CloudFormation parameters and that there is no staging environment.

---

## 6. Conventions

- **Routers stay thin.** Validate and delegate; business logic belongs in `services/`.
- **Config comes from environment variables** via `config.py` — never read
  `os.environ` directly inside a service.
- **Use the shared logger** (`from app.logger import get_logger`), not `print`.
- **Errors** go through `utils/error_utils.py` where the service has one, so API
  responses stay consistent.
- **Phone numbers** are normalised to E.164 (`+91…`). Dates are `YYYY-MM-DD`,
  times are `HH:MM` 24-hour.
- **Update the docs with the code.** If you change an endpoint, update the
  matching file in [docs_api/](docs_api/README.md) and its changelog.

---

## 7. Where to go next

| You want to… | Read |
|---|---|
| Understand the whole system | [HIGH_LEVEL_DESIGN.md](HIGH_LEVEL_DESIGN.md) |
| Know what is unfinished or risky | [HANDOVER.md](HANDOVER.md) |
| Find an endpoint | [LAMBDA_FUNCTIONS.md](LAMBDA_FUNCTIONS.md) |
| Call an endpoint | [docs_api/](docs_api/README.md) |
| Understand the data model | [DATABASE_TABLES.md](DATABASE_TABLES.md) |
| Deploy | [DEPLOYMENT.md](DEPLOYMENT.md) |
| Debug a production problem | [OPERATIONS_RUNBOOK.md](OPERATIONS_RUNBOOK.md) |
| Work on ABDM/ABHA | [ABDM_INTEGRATION.md](ABDM_INTEGRATION.md) |

Full index: [README.md](README.md).
