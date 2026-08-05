# Deployment

All infrastructure is deployed with AWS SAM from a single template. One command
deploys every Lambda; there is no per-service pipeline and no CI.

| | |
|---|---|
| **Stack name** | `sam-app` |
| **Region** | `ap-south-1` (Mumbai) |
| **API Gateway stage** | `prod` |
| **Runtime** | Python 3.11 |
| **Config file** | `samconfig.toml` |
| **Template** | `template.example.yaml` (see below) |

---

## Before your first deploy

Read [HANDOVER.md](HANDOVER.md) §2.1. In short:

- The **real `template.yaml` is not in git** — it contains live credentials and is
  gitignored. It must be obtained out-of-band.
- **`template.example.yaml` is the committed, parameterised equivalent.** It is
  identical in structure (same 54 resources) but every secret is a
  CloudFormation `Parameter`, so it is safe to commit and safe to deploy from
  once you supply values.

Deploy from `template.example.yaml` and pass secrets at deploy time. That is the
supported path going forward.

---

## Standard deploy

```bash
sam build --template template.example.yaml
```

```bash
sam deploy --template template.example.yaml --capabilities CAPABILITY_NAMED_IAM
```

`samconfig.toml` already supplies the stack name, region, S3 handling and
`confirm_changeset = true`, so SAM will show you a changeset and wait for
confirmation before touching anything. **Read the changeset** — it is the only
safety net; there is no staging environment.

### Supplying secrets

Secrets are `NoEcho` CloudFormation parameters. Pass them explicitly:

```bash
sam deploy --template template.example.yaml --capabilities CAPABILITY_NAMED_IAM --parameter-overrides JwtSecret=... BrevoSmtpKey=... RazorpayKeySecret=... OpenAiApiKey=...
```

That is unwieldy for 21 parameters, so put them in `samconfig.toml` instead —
**but note `samconfig.toml` is committed**, so only non-secret values belong
there. For secrets, either:

- keep a local, gitignored `samconfig-secrets.toml` and pass `--config-file`, or
- (preferred) move the values into AWS Secrets Manager / SSM Parameter Store and
  have the template resolve them, so no human handles a raw secret. This is the
  recommended long-term fix and has **not** been done yet.

Parameter names and which service uses each: [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md).

> `JwtSecret` is consumed by three services (`auth_lambda`, `abha_lambda`,
> `hospitals_lambda`). It must be the same value for all of them or tokens issued
> by one will fail validation in the others.

---

## Deploying a single function

`sam deploy` updates whatever changed, so a normal deploy is usually fine. To push
only one function's code without a full changeset:

```bash
sam build --template template.example.yaml AuthLambda
```

```bash
sam deploy --template template.example.yaml --capabilities CAPABILITY_NAMED_IAM
```

Logical IDs: `AuthLambda`, `UserServiceLambda`, `DoctorsServiceLambda`,
`BookingLambda`, `ChatLambda`, `ProcessPaymentLambda`, `DoctorPayoutsLambda`,
`ABHALambda`, `HospitalsLambda`, `MediLockerLambda`, `OCRWorkerLambda`.

---

## Function configuration

Memory and timeout are tuned per workload — worth knowing before you change them.

| Function | Memory | Timeout | Notes |
|---|---|---|---|
| `AuthLambda` | 256 MB | 30 s | |
| `UserServiceLambda` | 256 MB | 30 s | |
| `DoctorsServiceLambda` | 256 MB | 30 s | |
| `BookingLambda` | 256 MB | 30 s | |
| `ChatLambda` | 256 MB | 30 s | Calls RAG server then LLM |
| `ProcessPaymentLambda` | 256 MB | 30 s | |
| `DoctorPayoutsLambda` | 256 MB | 30 s | |
| `HospitalsLambda` | 256 MB | 30 s | |
| `ABHALambda` | 512 MB | 30 s | Crypto (X25519/AES-GCM) is CPU-bound |
| `MediLockerLambda` | 1024 MB | 120 s | OCR + LLM extraction |
| `OCRWorkerLambda` | 512 MB | 300 s | SQS-triggered; must stay ≤ queue visibility timeout |

**Constraint:** `OCRQueue` has `VisibilityTimeout: 300`, which must be greater
than or equal to `OCRWorkerLambda`'s timeout. If you raise the Lambda timeout,
raise the queue's visibility timeout first, or messages will be redelivered while
still being processed.

The API Gateway hard limit is **29 seconds**, so any HTTP-facing function with a
timeout above that (i.e. `MediLockerLambda` at 120 s) will still return a gateway
timeout to the caller even though the Lambda keeps running. Long Medilocker
operations should go through the async OCR path (`POST /medilocker/upload/async`)
rather than blocking the request.

---

## The OCR layer

`ocr_layer/` is published as a Lambda layer (`ocr-core`) providing the shared
Textract wrapper, and is attached to `MediLockerLambda` and `OCRWorkerLambda`.

It uses `BuildMethod: makefile` deliberately — the default python3.11 build
method nests the package as `python/python/ocr_core`, which breaks
`import ocr_core`. If you change the layer build, verify the import still
resolves inside the deployed function, not just locally.

---

## Verifying a deploy

```bash
aws cloudformation describe-stacks --stack-name sam-app --region ap-south-1 --query 'Stacks[0].StackStatus'
```

Then smoke-test the affected service — every FastAPI app serves its own
OpenAPI docs, and [docs_api/](docs_api/README.md) has ready-made request bodies.

Watch logs live for the function you just changed:

```bash
sam logs --stack-name sam-app --name AuthLambda --region ap-south-1 --tail
```

---

## Rolling back

CloudFormation rolls back automatically if a deploy fails. To undo a *successful*
deploy you must redeploy the previous code — there is no one-command rollback,
because the template is not versioned in git (see [HANDOVER.md](HANDOVER.md) §2.1).

```bash
git checkout <previous-commit> -- <service>_lambda/
```

```bash
sam build --template template.example.yaml && sam deploy --template template.example.yaml --capabilities CAPABILITY_NAMED_IAM
```

**This is a real gap.** Committing `template.example.yaml` and keeping it in sync
is what makes infrastructure rollback possible at all — keep it updated whenever
you add or rename a Lambda, table or environment variable.

---

## Things that will bite you

- **There is no staging.** `samconfig.toml` defines one stack and API Gateway has
  one stage, `prod`. Every deploy is to production.
- **There is no CI and almost no tests.** Nothing verifies a deploy except you.
- **DynamoDB tables are declared in the template.** A rename or a changed key
  schema means CloudFormation **replaces** the table — silently discarding its
  data. Check the changeset for `Replacement: True` before confirming.
- **The IAM role is shared.** All functions use one `LambdaExecutionRole` with
  `dynamodb:*`. Adding a permission for one service grants it to all of them.
- **`.aws-sam/` is stale build output.** If a change does not seem to take effect,
  `rm -rf .aws-sam` and rebuild.
