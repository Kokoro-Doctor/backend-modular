# Clinical Query History Design

## Overview

Currently, the `clinical-query` endpoint (`/medilocker/users/{user_id}/clinical-query`) is stateless. It builds a patient context from uploaded documents and answers a single question. To support follow-up questions (e.g., "What specific medication was mentioned in that report?"), we need to persist the conversation history.

This document outlines the design for storing and retrieving clinical query history.

## Database Design

We should introduce a **new DynamoDB table** (or use a distinct partition strategy) to store these clinical conversations. This ensures separation of concerns between:
1.  **General Chat (`ChatHistory` table)**: Patient chatting with the AI Assistant.
2.  **Clinical Query (`ClinicalQueryHistory` table)**: Doctor asking questions about a Patient's records.

### Proposed Table: `MedilockerClinicalChats`

| Attribute | Type | Key Type | Description |
|-----------|------|----------|-------------|
| `session_id` | String | PK | Unique identifier for the conversation session (or `patient_id` if single-threaded). |
| `timestamp` | Number | SK | Unix timestamp of the message. |
| `patient_id` | String | GSI-PK | The ID of the patient being queried. |
| `doctor_id` | String | | The ID of the doctor performing the query (if available). |
| `question` | String | | The doctor's question. |
| `answer` | String | | The AI's response. |
| `created_at` | String | | ISO 8601 datetime. |

> **Note on `session_id` vs `patient_id`**: 
> If we want to support multiple doctors querying the same patient simultaneously without interference, or distinct "sessions" of analysis, the Frontend should generate a `session_id` (UUID) and pass it with the request.
> If we only want a single persistent history per patient (shared by all doctors), we can use `patient_id` as the PK.
> 
> **Recommendation**: Use `session_id` as PK to support distinct conversations.

## Integration Plan

### 1. Update Request Schema

Modify `ClinicalQueryRequest` in `app/models/schemas.py` to include an optional `session_id`.

```python
class ClinicalQueryRequest(BaseModel):
    question: str
    session_id: Optional[str] = None  # New field
```

### 2. Infrastructure (DynamoDB)

Add the `MedilockerClinicalChats` table to `backend/medilocker_lambda/template.yaml` (or equivalent infrastructure config).

```yaml
MedilockerClinicalChats:
  Type: AWS::DynamoDB::Table
  Properties:
    TableName: !Sub ${EnvironmentName}-MedilockerClinicalChats
    AttributeDefinitions:
      - AttributeName: session_id
        AttributeType: S
      - AttributeName: timestamp
        AttributeType: N
    KeySchema:
      - AttributeName: session_id
        KeyType: HASH
      - AttributeName: timestamp
        KeyType: RANGE
    BillingMode: PAY_PER_REQUEST
```

### 3. Service Layer (`clinical_query_service.py`)

Update `answer_clinical_query` to:
1.  **Fetch History**: If `session_id` is provided, fetch recent messages from `MedilockerClinicalChats`.
2.  **Append to Context**: Add previous Q&A pairs to the LLM prompt.
3.  **Save Interaction**: After generating the answer, save the `{question, answer}` pair to `MedilockerClinicalChats`.

### 4. Router Layer (`medilocker_router.py`)

Update the endpoint to handle the `session_id`.

```python
@router.post("/users/{user_id}/clinical-query")
async def clinical_query(
    user_id: str = Path(...),
    payload: ClinicalQueryRequest = Body(...),
):
    # ... existing validation ...
    
    # Pass payload.session_id to service
    result = answer_clinical_query(
        patient_context=patient_context,
        question=payload.question,
        client=openai_client,
        session_id=payload.session_id, # New arg
        user_id=user_id # For storing in DB
    )
    return result
```

## Prompt Engineering

The system prompt in `clinical_query_service.py` needs to be updated to acknowledge the history.

**Current:**
> "You are a clinical medical assistant..."

**Updated:**
> "You are a clinical medical assistant...
> 
> PREVIOUS CONVERSATION:
> {history_text}
> 
> Use the context above and the previous conversation to answer the follow-up question."

## Implementation Checklist

- [ ] Create `MedilockerClinicalChats` DynamoDB table.
- [ ] Add `MedilockerClinicalChats` to `app/config.py` environment variables.
- [ ] Create `app/services/clinical_chat_service.py` (or add to `document_db_service.py`) to handle DB operations (put/query).
- [ ] Update `ClinicalQueryRequest` schema.
- [ ] Update `answer_clinical_query` logic to fetch/save history.
