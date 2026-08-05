# Operations runbook

How to find out what is wrong in production and what to do about it.

**Context you need up front:** there is one environment (`prod`), no CI, no
alarms configured in the template, and one shared IAM role. Diagnosis is
CloudWatch logs plus direct DynamoDB inspection.

| | |
|---|---|
| Stack | `sam-app` |
| Region | `ap-south-1` |
| Log groups | `/aws/lambda/<FunctionName>` |
| API stage | `prod` |

---

## Reading logs

Tail one function while reproducing a problem:

```bash
sam logs --stack-name sam-app --name MediLockerLambda --region ap-south-1 --tail
```

Search a window for errors:

```bash
aws logs filter-log-events --log-group-name /aws/lambda/MediLockerLambda --region ap-south-1 --start-time $(( ($(date +%s) - 3600) * 1000 )) --filter-pattern 'ERROR'
```

Every service uses the shared structured logger (`app/logger.py`), so messages
are prefixed with the module that emitted them (`[jwt_auth]`, `[ocr_service]`,
and so on) — filter on that prefix to follow one subsystem.

Function names: `AuthLambda`, `UserServiceLambda`, `DoctorsServiceLambda`,
`BookingLambda`, `ChatLambda`, `ProcessPaymentLambda`, `DoctorPayoutsLambda`,
`ABHALambda`, `HospitalsLambda`, `MediLockerLambda`, `OCRWorkerLambda`.

---

## Triage by symptom

### Every endpoint on one service returns 502

Almost always an import-time crash — the handler never starts, so there is no
application log line, only an `Unable to import module` or a traceback at the
very top of the log stream.

Usual causes, in order of likelihood:

1. A **missing required env var**. `auth_lambda/config.py` uses
   `os.environ["JWT_SECRET"]`, which raises `KeyError` at import if unset. Other
   services use `.get()` with defaults and fail later and more confusingly.
2. A **missing dependency** — added to the venv locally but not to
   `requirements.txt`.
3. A **broken layer import** on `MediLockerLambda` / `OCRWorkerLambda` — if the
   `ocr-core` layer was rebuilt without `BuildMethod: makefile`, `import ocr_core`
   fails (see [DEPLOYMENT.md](DEPLOYMENT.md) §The OCR layer).

Check the deployed configuration:

```bash
aws lambda get-function-configuration --function-name AuthLambda --region ap-south-1 --query 'Environment.Variables'
```

### 401/403 everywhere after a deploy

`JWT_SECRET` no longer matches across `auth_lambda`, `abha_lambda` and
`hospitals_lambda`. Tokens minted by auth will not validate elsewhere. Compare
all three and redeploy with one consistent value — note this also invalidates
every session already issued. See [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md).

### Requests time out at ~29 seconds

API Gateway's hard limit is 29 s regardless of the Lambda timeout.
`MediLockerLambda` has a 120 s timeout, so slow OCR/LLM work returns a gateway
timeout to the client while the Lambda keeps running and still bills.

Route long work through the async path (`POST /medilocker/upload/async`, then
poll `GET /medilocker/users/{user_id}/files/{file_id}/status`) rather than
raising timeouts.

### OCR jobs never complete

The pipeline is: producer (`medilocker` or `hospitals`) → `OCRQueue` →
`OCRWorkerLambda` → `MedilockerDocuments`.

```bash
aws sqs get-queue-attributes --queue-url <OCRQueue-url> --region ap-south-1 --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
```

- **Messages piling up, worker not invoked** → check the event-source mapping is
  enabled and the worker is not erroring at import.
- **Messages in `OCRQueueDLQ`** → the worker failed three times on the same
  message (`maxReceiveCount: 3`). Read the DLQ body for `file_id` and `s3_key`,
  then search `/aws/lambda/OCRWorkerLambda` for that `file_id`. DLQ retention is
  14 days.
- **Same message processed repeatedly** → the worker is exceeding the queue's
  300 s visibility timeout. Both must move together.
- **Worker succeeds but nothing appears** → producer and consumer disagree about
  `DOCUMENTS_TABLE` or `S3_FOLDER_PREFIX`.

### Payments succeed but no subscription appears

The chain is: Razorpay webhook → `ProcessPaymentLambda` → direct Lambda invoke of
`BookingLambda` → `UserDoctorSubscriptions`, plus a `DoctorEarningsLedger` entry.

Check in this order:

1. Did the webhook arrive? Look in `/aws/lambda/ProcessPaymentLambda`. If not,
   check the webhook is registered and reachable in the Razorpay dashboard.
2. Signature rejected? `RAZORPAY_WEBHOOK_SECRET` must match the value configured
   on the webhook in Razorpay. A mismatch looks like a silent drop.
3. Did the `BookingLambda` invoke fail? `SUBSCRIPTION_SERVICE_LAMBDA_NAME` must
   be `BookingLambda`, and both services must agree on the subscription table names.
4. Ledger written? Check `DOCTOR_EARNINGS_LEDGER_TABLE` — payouts read from it,
   so a gap here surfaces later as missing doctor earnings.

Background: [PAYMENT_SYSTEM_ANALYSIS.md](PAYMENT_SYSTEM_ANALYSIS.md).

### Emails or OTPs not arriving

All transactional email goes through Brevo (`BREVO_*`) from `auth_lambda` and
`payment_lambda`; SMS OTP goes through AWS SNS.

- Check the sending service's logs for SMTP errors — an expired or rotated
  `BREVO_SMTP_KEY` is the usual cause, and it breaks signup, verification,
  password reset and payment receipts at once.
- For SMS, confirm the AWS account is out of the SNS sandbox and has not hit its
  monthly spend limit — SNS drops messages silently once capped.
- Auth applies rate limits per identifier; repeated requests during testing can
  legitimately be throttled.

### Chat returns errors or generic answers

`chat_lambda` tries the RAG server first and falls back to the LLM.

- `RAG_SERVER_URL` is `http://13.203.1.165:8000/rag` — a hardcoded IP on plain
  HTTP that is **not** part of this SAM stack. If that host is down, chat
  degrades to LLM-only. Ownership is unresolved ([HANDOVER.md](HANDOVER.md) §4).
- Otherwise check `OPENAI_API_KEY` / `GROQ_API_KEY` for quota or billing errors;
  `utils/openai_errors.py` and `utils/rag_errors.py` classify these.

### ABDM / ABHA flows hang after the first call

ABDM is asynchronous: you call the gateway, it calls back on
`/api/v3/…`. If callbacks never arrive nothing errors — the flow just stops.

1. `KOKORO_WEBHOOK_BASE_URL` must be the public API Gateway URL and reachable
   from the ABDM gateway.
2. Correlate using `request_id` in `AbdmTransactions`
   (`GET /abha/transactions`) — the callback body carries `response.requestId`.
3. Callback handlers orchestrate synchronously inside a 30 s Lambda, so a slow
   downstream call can blow the timeout mid-flow and leave state half-written.

Detail: [ABDM_INTEGRATION.md](ABDM_INTEGRATION.md).

### Throttling or 5xx under load

DynamoDB tables and Lambda concurrency are the usual limits. Check
`ThrottledRequests` on the table and `Throttles` on the function in CloudWatch
metrics. Note the shared `LambdaExecutionRole` means one service's runaway
retry loop can exhaust account-level concurrency for all of them.

---

## Common inspections

```bash
aws dynamodb get-item --table-name Users --region ap-south-1 --key '{"phoneNumber":{"S":"+919999999999"}}'
```

```bash
aws lambda get-function-configuration --function-name ABHALambda --region ap-south-1 --query '{Timeout:Timeout,Memory:MemorySize,Runtime:Runtime}'
```

```bash
aws cloudformation describe-stack-events --stack-name sam-app --region ap-south-1 --max-items 20
```

Table schemas and key layouts: [DATABASE_TABLES.md](DATABASE_TABLES.md).

---

## What is missing (and worth adding)

This runbook is thinner than it should be because the observability is thin.
In rough priority order:

1. **CloudWatch alarms** — none are defined in the template. At minimum: Lambda
   error rate, DLQ depth, DynamoDB throttling.
2. **A staging environment.** One stack, one stage, every deploy is production.
3. **CI running the existing tests** — there are only three test files today, but
   nothing runs even those.
4. **Per-function IAM roles.** One shared role with `dynamodb:*` means any
   compromised service reaches all patient data.
5. **Structured request IDs end to end**, so a single user action can be traced
   across API Gateway, Lambda and SQS.
6. **Dashboards** for the payment and OCR pipelines, which are the two flows
   where silent failure is most costly.
