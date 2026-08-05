# ABDM / ABHA integration

> **Status as of 5 August 2026.** This is the largest in-flight workstream in the
> backend and the one with external compliance deadlines. Read this before
> touching anything under `abha_lambda/`.

All code lives in `abha_lambda/`. API Gateway routes `/abha/{proxy+}` and
`/api/v3/{proxy+}` to `ABHALambda`.

---

## 1. What Kokoro is, in ABDM terms

Kokoro operates as a **single HRP (Health Repository Provider) / bridge fronting
N hospital HIP service-IDs**. There is one ABDM `client_id`/`client_secret` for
the whole platform; the per-hospital `X-HIP-ID` is resolved at request time from
the `HospitalAbdmConfig` table.

This matters: hospitals do not each hold ABDM credentials. Kokoro's bridge
registration is what the ABDM gateway sees, and per-hospital identity is a
routing concern inside our code.

```
Patient (ABHA / PHR app)
        │
        ▼
  ABDM Gateway ──────────── consent manager (CM)
        │  ▲
        │  │  async callbacks → /api/v3/*
        ▼  │
   ABHALambda  (bridge: HIP + HIU)
        │
        ├── AbhaAccounts          (ABHA identity + ABDM tokens)
        ├── HospitalAbdmConfig    (hospital → HIP/HIU id, bridge URL)
        ├── AbdmTransactions      (request_id correlation, TTL 90d)
        ├── ConsentArtefacts      (HIP-side granted consents)
        ├── HiuConsentRequests    (HIU-side consent requests)
        └── HiuDataRequests       (HIU-side data requests + ephemeral keys)
```

A full consent-and-data-flow diagram is in
[ABDM_M2_FLOW_DIAGRAM.md](ABDM_M2_FLOW_DIAGRAM.md).

**Everything is asynchronous.** You call the gateway, it calls back. Correlation
is by `request_id`, tracked in `AbdmTransactions` and inspectable via
`GET /abha/transactions`. If a callback never arrives, nothing errors — the flow
simply stops. That is the single most common confusion when debugging.

---

## 2. Milestone status

### Milestone 1 — ABHA create / login / profile / card ✅ works

`app/routers/abha_router.py`, `services/abha_service.py`, `abha_accounts_service.py`

Aadhaar and mobile-driven ABHA creation and login, profile and card retrieval.
ABDM tokens are stored server-side in `AbhaAccounts` and resolved per request —
clients never pass an ABHA token.

**Known gaps:**
- Endpoint authentication is inconsistent — several M1 routes have no Kokoro JWT
  requirement.
- The `kokoro_user_id` GSI binding on `AbhaAccounts` is effectively dead: the
  attribute is never written, so ABHA records cannot reliably be joined back to
  Kokoro users.

### Milestone 2 — HIP linking and data flow ⚠️ partially complete

`hip_linking_router.py`, `services/hip_linking_service.py`, `data_flow_service.py`,
`consent_service.py`, `abdm/data_encryption.py`

**Working:**
- Care-context linking (ABDM 4.3.x) and token generation.
- `AbdmTransactions` correlation across the async callbacks.
- **ECDH encryption is fully implemented** — X25519 → HKDF-SHA256 →
  AES-256-GCM, both encrypt and decrypt, in `abdm/data_encryption.py`, with test
  vectors in `tests/test_fidelius.py` / `vectors_fidelius.json`. This is verified
  against the Fidelius reference implementation in `tools/fidelius-cli/`.

**The blocking gap — no clinical data is actually shared:**

`_build_fhir_bundle()` in `services/data_flow_service.py:209` still returns an
empty placeholder bundle:

```python
{"resourceType": "Bundle", "type": "document",
 "meta": {"careContextReference": ...}, "entry": []}
```

The whole surrounding flow (consent → request → encrypt → push → notify) runs
end to end, but transfers **no records**. Completing M2 means building a real
read path from Kokoro's clinical data (Medilocker documents, prescriptions,
diagnoses) into ABDM-compliant FHIR R4 bundles matching the `hiTypes` the
consent granted. This is the largest single piece of remaining work.

**Also missing:** consent signature verification, and enforcement of consent
state, date ranges and `hiType` scope before data is released.

### Milestone 3 — HIU (consuming records from other providers) ✅ implemented

`hiu_router.py`, `services/hiu_consent_service.py`, `hiu_data_service.py`,
`abdm/hiu_client.py`

Implemented 2026-06-03. Consent request / status / fetch, health-information
requests, and five inbound HIU callbacks (`on-init`, `notify` with auto-ack per
4.3.4, `on-status`, `on-fetch`, and `health-information/transfer`, which decrypts
the payload and notifies the CM). `register-facility` sets `hiu_id` = `hip_id`.

**Requires `KOKORO_WEBHOOK_BASE_URL`** to be set correctly — it builds the HIU
`dataPushUrl`. If it is wrong, remote providers cannot deliver data and the flow
stalls silently.

---

## 3. Structural blockers

These are architectural, not missing features. They should be addressed before
the integration is put in front of real patient data at volume.

| # | Blocker | Why it matters |
|---|---|---|
| 1 | **Inbound callbacks are unauthenticated** | Any caller who knows a `/api/v3/*` path can post a forged callback and drive state transitions |
| 2 | **Synchronous orchestration inside the 6.3.3 callback** on a 30 s Lambda | A slow downstream call blows the timeout mid-flow and leaves state half-written. Needs decoupling via SQS or Step Functions |
| 3 | **No idempotency or replay protection** | ABDM retries callbacks; duplicates are processed as if new |
| 4 | **Secrets hardcoded** in `template.yaml` (see [HANDOVER.md](HANDOVER.md) §3) | `ABDM_CLIENT_SECRET` among them |
| 5 | **`list_recent` performs an unscoped table scan** | Leaks data across hospitals — a multi-tenant isolation failure |
| 6 | **Ephemeral X25519 private keys stored unwrapped** in `HiuDataRequests` | Should be KMS-wrapped at rest |

Recommended sequencing: **security baseline (1, 3, 6) → async decoupling (2) →
FHIR/clinical bridge (the M2 gap) → tenant isolation (5).**

---

## 4. Configuration

| Variable | Sandbox value | Notes |
|---|---|---|
| `ABDM_CLIENT_ID` | `SBXID_023051` | Per-platform, not per-hospital |
| `ABDM_CLIENT_SECRET` | 🔑 | Rotate — see [HANDOVER.md](HANDOVER.md) §3 |
| `ABDM_GATEWAY_BASE_URL` | `https://dev.abdm.gov.in` | |
| `ABDM_ABHA_BASE_URL` | `https://abhasbx.abdm.gov.in` | |
| `ABDM_X_CM_ID` | `sbx` | `abdm` in production |
| `KOKORO_WEBHOOK_BASE_URL` | API Gateway prod URL | Must be publicly reachable from the ABDM gateway |
| `ABDM_TRANSACTION_TTL_DAYS` | `90` | `AbdmTransactions` TTL |

**The integration currently runs against the ABDM sandbox** (`SBX…`, `dev.abdm.gov.in`,
`X-CM-ID: sbx`). Production onboarding is a separate NHA process; confirm its
status with whoever owns the ABDM account ([HANDOVER.md](HANDOVER.md) §4).

---

## 5. Code map

```
abha_lambda/app/
├── routers/
│   ├── abha_router.py         # M1: create/login/profile/card        (/abha)
│   ├── hip_linking_router.py  # M2: linking + bridge admin           (/abha)
│   ├── hiu_router.py          # M3: HIU consent + data               (/abha/hiu)
│   └── webhook_router.py      # ALL inbound ABDM callbacks           (/api/v3)
├── services/
│   ├── abha_service.py, abha_accounts_service.py, user_abha_service.py
│   ├── hip_linking_service.py, consent_service.py, data_flow_service.py
│   ├── hiu_consent_service.py, hiu_data_service.py
│   ├── hospital_abdm_service.py      # hospital → HIP/HIU id resolution
│   └── abdm_transactions_service.py  # request_id correlation
└── abdm/
    ├── client.py, hip_client.py, hiu_client.py   # gateway HTTP clients
    ├── token_manager.py                          # ABDM session tokens
    ├── data_encryption.py                        # X25519/HKDF/AES-GCM ✅
    ├── encryption.py, fidelius.py                # Fidelius-compatible crypto
    ├── certificate_manager.py
    └── schemas.py
```

`tools/fidelius-cli/` holds the ABDM reference implementation for FHIR
encryption, including NHA's *"Encryption and Decryption Implementation Guidelines
for FHIR data in ABDM"*. Use it to validate crypto changes — `tests/test_fidelius.py`
checks our implementation against vectors generated from it.

---

## 6. Canonical specifications live in Notion

The authoritative ABDM specifications are **not** mirrored into this repo. Under
the Notion parent page **"ABDM ABHA V3 APIs"**:

- **Milestone 1 / Milestone 2 / Milestone 3** pages — endpoint-level specs
- **"ABDM / WASA Compliance Audit"** — component-level audit, dated 2 June 2026

Confirm the team has Notion access as part of the handover. Without these pages,
the remaining ABDM work cannot be specified correctly.

---

## 7. Debugging

- Correlate everything by `request_id` — `GET /abha/transactions` reads
  `AbdmTransactions`.
- Inbound callbacks carry `response.requestId`; match that to the transaction.
- A flow that "does nothing" is almost always a callback that never arrived:
  check `KOKORO_WEBHOOK_BASE_URL` and that the gateway can reach it.
- Fixed callback paths include `/api/v3/hip/token/on-generate-token`,
  `/api/v3/link/on_carecontext`, `/api/v3/consent/request/hip/notify`,
  `/api/v3/hip/health-information/request`, and the five `/api/v3/hiu/*` routes.

Request and response examples: [docs_api/abha-lambda.md](docs_api/abha-lambda.md)
(the most detailed API doc in the repo, ~2,500 lines).
