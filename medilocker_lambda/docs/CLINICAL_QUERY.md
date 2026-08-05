# Clinical Query

## Overview

The clinical query endpoint lets a doctor ask natural-language questions about a patient's stored medical records. The system builds context from uploaded documents (OCR + structured extraction) and uses GPT-4o to return concise, factual answers.

**This is separate from prescription generation.** Prescription produces a consolidated medical summary; clinical query answers specific questions (e.g., "What medications is the patient currently on?", "When was the last HbA1c?").

---

## Endpoint

```
POST /medilocker/users/{user_id}/clinical-query
```

**Path:** `user_id` — Patient ID (owner of the documents being queried).

**Request:**
```json
{
  "question": "What medications were prescribed in the latest report?"
}
```

**Response:**
```json
{
  "answer": "Based on the most recent document, the patient was prescribed Metformin 500mg twice daily and Atorvastatin 10mg at night."
}
```

---

## Flow

```
Request: POST /medilocker/users/{user_id}/clinical-query
  │
  ▼
Step 1: Query DynamoDB
  └── get_latest_documents(user_id, limit=PRESCRIPTION_MAX_DOCS)
  │
  ▼
Step 2: Filter
  └── Keep only docs where ocr_status == COMPLETED
      AND structured_status == COMPLETED
  │
  ▼
Step 3: Build patient context
  └── context_service.build_patient_context(valid_documents)
  │
  ▼
Step 4: GPT-4o call
  └── clinical_query_service.answer_clinical_query(
        patient_context, question, client
      )
  │
  ▼
Response: { "answer": "..." }
```

**GPT calls:** 1 per request. No OCR or extraction at query time — all data comes from pre-processed documents in DynamoDB.

---

## Current Behavior (Stateless)

- Each request is **independent**. No conversation history is stored.
- Follow-up questions (e.g., "What was the dose of that medication?") have no prior context.
- To support follow-ups, history must be persisted. See `CLINICAL_QUERY_HISTORY.md` for the design.

---

## System Prompt

The clinical assistant prompt enforces:

- Use **only** provided information
- Never hallucinate diagnoses or medications
- If information is missing, say it is not available
- Prefer newer documents when conflicts exist
- Be concise, clinical, and factual
- Answer as if speaking to a medical professional
- No disclaimers

---

## Edge Cases

| Condition | Result |
|-----------|--------|
| No documents for user | `{ "answer": "No medical documents available for this patient." }` |
| Documents exist but none have `COMPLETED` status | `{ "answer": "No processed medical data available." }` |
| `OPENAI_API_KEY` not set | 500 — "OpenAI API key not configured" |

---

## Service Functions

| Function | Location | Purpose |
|----------|----------|---------|
| `answer_clinical_query(patient_context, question, client)` | clinical_query_service | Single GPT-4o call with clinical assistant prompt |
| `build_patient_context(documents)` | context_service | Builds patient context from document records |

---

## Related Docs

| Doc | Contents |
|-----|----------|
| `CLINICAL_QUERY_HISTORY.md` | Design for follow-up questions — DynamoDB table, session_id, implementation plan |
| `PRESCRIPTION_GENERATION.md` | Prescription pipeline (Flow A & B) — separate from clinical query |
| `MEDILOCKER_FLOW.md` | Complete Medilocker system documentation |
