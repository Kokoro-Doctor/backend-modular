# Chat Lambda Flow Documentation

## Overview
The Chat Lambda is a FastAPI-based AWS Lambda function that handles conversational AI interactions for Kokoro Doctor. It provides health-related chat functionality with RAG (Retrieval-Augmented Generation) capabilities and LLM fallback.

## Architecture

```
┌─────────────────┐
│   AWS Lambda    │
│   (Mangum)      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   FastAPI App   │
│   (main.py)     │
└────────┬────────┘
         │
         ├──► CORS Middleware
         │
         ▼
┌─────────────────┐
│  Chat Router    │
│ (chat_router.py)│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Chat Service   │
│(chat_service.py)│
└────────┬────────┘
         │
         ├──► DynamoDB (Chat History)
         ├──► RAG Server (Primary)
         └──► OpenAI LLM (Fallback)
```

## Request Flow

### 1. Entry Point (`main.py`)

**Handler**: `handler = Mangum(app)`
- AWS Lambda invokes the handler via Mangum adapter
- Mangum converts AWS Lambda events to ASGI requests for FastAPI

**FastAPI App Setup**:
- Configures CORS middleware for:
  - `https://kokoro.doctor` (production)
  - `http://localhost:8081` (local development)
- Custom HTTP exception handler preserves CORS headers on errors
- Includes chat router at root level

### 2. Request Validation (`chat_router.py`)

**Endpoint**: `POST /chat/send`

**Request Body** (`ChatRequest`):
```json
{
  "user_id": "user-123",
  "session_id": null,
  "doctor_id": null,
  "message": "What is high blood pressure?",
  "language": "en",
  "role": "patient",
  "chat_count": null
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| user_id | string | One of user_id, session_id, doctor_id | Patient identifier |
| session_id | string | One of user_id, session_id, doctor_id | Anonymous session identifier |
| doctor_id | string | One of user_id, session_id, doctor_id | Doctor identifier |
| message | string | ✅ | User's message |
| language | string | No (default: "en") | Language code (en, hi, es, te) |
| role | string | No (default: "patient") | "doctor" or "patient" |
| chat_count | number | No | Current chat count for anonymous users |

**Validation Steps**:
1. ✅ Check if `message` is non-empty (raises 400 if empty)
2. ✅ Extract identifier from `user_id`, `session_id`, or `doctor_id` (raises 400 if none provided)
3. ✅ Call `process_chat_message()` with identifier, message, language, and role

### 3. Message Processing (`chat_service.py`)

#### Step 3.1: Retrieve Chat History
**Function**: `get_chat_history(identifier: str)`

- Queries DynamoDB table using `user_id` as partition key
- Retrieves last 5 messages (most recent first, then reversed)
- Returns list of formatted messages: `["user: message1", "bot: response1", ...]`
- Returns empty list on error (non-blocking)

**DynamoDB Query**:
- Table: `DYNAMODB_TABLE` (from environment)
- Region: `ap-south-1`
- Query: `KeyConditionExpression="user_id = :id_value"`
- Limit: 5 messages
- Order: Most recent first (`ScanIndexForward=False`)

#### Step 3.2: Try RAG Server (Primary)
**Function**: `call_rag_server(message: str, language: str)`

**Flow**:
1. Sends POST request to `RAG_SERVER_URL` with:
   ```json
   {
     "message": "user message",
     "language": "en"
   }
   ```
2. Timeout: 60 seconds
3. **Success Criteria**:
   - Status code 200
   - Response contains non-empty `response` field
   - Response is not "none" (case-insensitive)
4. **Failure Handling**:
   - Any exception → logs warning → returns `None`
   - Non-200 status → logs warning → returns `None`
   - Empty/invalid response → returns `None`
   - Triggers LLM fallback

#### Step 3.3: Fallback to LLM (if RAG fails)
**Function**: `call_llm_api(history: List[str], user_question: str, language: str)`

**Intent Detection**:
- Analyzes user message for keywords:
  - **Heart Health**: "heart", "bp", "blood pressure", "cardio", "cholesterol", "pulse", "ecg", "angina", "palpitation"
  - **Reproductive Health**: "period", "pregnancy", "fertility", "sex", "menstruation", "ovulation", "contraceptive", "hormone"
  - **General**: Default category

**Prompt Construction**:
- System message: Defines AI as empathetic health companion
- Context: Includes last 5 messages from history
- Instructions:
  - Provide SPECIFIC, ACTIONABLE guidance
  - Avoid generic "consult doctor" responses
  - Address user's exact concern
  - Be conversational and personalized
  - Support multiple languages (en, hi, es, te)
  - First message includes welcome message

**OpenAI API Call**:
- Model: `gpt-3.5-turbo`
- Temperature: 0.7
- Messages:
  - System: Role definition
  - User: Full prompt with context and question
- Returns: Generated response text

**Error Handling**:
- Missing API key → raises HTTPException 500
- API failure → raises HTTPException 503

#### Step 3.4: Store Conversation
**Function**: `store_message(identifier: str, user_message: str, bot_message: str)`

**DynamoDB Write**:
- Table: `DYNAMODB_TABLE`
- Item structure:
  ```json
  {
    "user_id": "identifier",
    "timestamp": 1234567890,
    "created_at": "2026-02-22T10:04:59+00:00",
    "date_bucket": "2026-02-22",
    "user_message": "user's message",
    "bot_message": "bot's response"
  }
  ```
- Partition key: `user_id`
- Sort key: `timestamp`
- Raises HTTPException 500 on storage failure

### 4. Response Flow

**Success Response Body**:
```json
{
  "text": "High blood pressure, also known as hypertension, is when your blood pressure is consistently above 130/80 mmHg...",
  "is_preview": false
}
```

**Error Responses**:
- `400`: Missing message or identifier
- `500`: DynamoDB storage failure or missing OpenAI API key
- `503`: OpenAI API failure

## Complete Request-Response Cycle

```
1. Client → POST /chat/send
   Body: {
     "user_id": "user123",
     "message": "What is high blood pressure?",
     "language": "en",
     "role": "patient"
   }

2. FastAPI → CORS check → Route to /chat/send

3. Router → Validate request
   ✅ Message exists
   ✅ Identifier exists (user_id)

4. Service → Get chat history from DynamoDB
   Query: user_id = "user123"
   Result: Last 5 messages (if any)

5. Service → Try RAG Server
   POST RAG_SERVER_URL
   Payload: {"message": "...", "language": "en"}
   
   IF RAG_SUCCESS:
     6a. Store message in DynamoDB
     7a. Return RAG response
   
   ELSE (RAG_FAIL):
     6b. Detect intent (Heart Health / Reproductive / General)
     7b. Build LLM prompt with history + intent
     8b. Call OpenAI API (gpt-3.5-turbo)
     9b. Store message in DynamoDB
     10b. Return LLM response

6. Response → Client
   {
     "text": "High blood pressure, also known as hypertension..."
   }
```

## Environment Variables

Required environment variables:
- `DYNAMODB_TABLE`: DynamoDB table name for chat history
- `OPENAI_API_KEY`: OpenAI API key for LLM fallback
- `RAG_SERVER_URL`: URL of the RAG server endpoint

## Key Features

1. **Multi-Identifier Support**: Supports `user_id`, `session_id`, or `doctor_id` for flexible user identification
2. **RAG-First Architecture**: Attempts RAG server first for domain-specific responses
3. **LLM Fallback**: Falls back to OpenAI LLM if RAG is unavailable
4. **Chat History**: Maintains conversation context (last 5 messages)
5. **Multi-Language Support**: Supports English, Hindi, Spanish, and Telugu
6. **Intent Detection**: Automatically detects Heart Health vs Reproductive Health queries
7. **Conversation Persistence**: Stores all conversations in DynamoDB
8. **Error Resilience**: Graceful degradation (RAG → LLM → Error)
9. **Chat History Queries**: User-level and global analytics via Query-only DynamoDB access

---

## API Endpoints Summary

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | /chat/send | JSON | Send a message, get bot response |
| GET | /chat/history/user | Query params | User's chat history (filter by date/range) |
| GET | /chat/history/global | Query params | All users' chat history (date/range/days) |

---

## Chat History Endpoints

### Storage Schema

Every message stored in DynamoDB now includes:

| Attribute | Type | Description |
|---|---|---|
| user_id | String (PK) | Identifier |
| timestamp | Number (SK) | Unix UTC |
| created_at | String | ISO-8601 datetime |
| date_bucket | String | `YYYY-MM-DD` (GSI partition key) |
| user_message | String | User's message |
| bot_message | String | Bot's response |

### DynamoDB Indexes

| Index | PK | SK | Purpose |
|---|---|---|---|
| Table primary key | user_id | timestamp | Per-user queries |
| date-timestamp-index (GSI) | date_bucket | timestamp | Global / cross-user queries |

### GET /chat/history/user

Returns chat history for a single user.

**Request (query params):**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| identifier | string | ✅ | User/session/doctor id |
| date | string | No | Single date `YYYY-MM-DD` |
| start_date | string | No* | Range start `YYYY-MM-DD` (*both required for range) |
| end_date | string | No* | Range end `YYYY-MM-DD` (*both required for range) |
| limit | number | No (default 50) | Max messages to return (1–500) |

**Example request:**
```
GET /chat/history/user?identifier=user-123&limit=20
GET /chat/history/user?identifier=user-123&date=2026-02-22
GET /chat/history/user?identifier=user-123&start_date=2026-02-01&end_date=2026-02-22
```

**Validation:** Cannot combine `date` with a date range. Both `start_date` and `end_date` required for range.

**DynamoDB:** Uses table primary key — always a Query, never a Scan.

### GET /chat/history/global

Returns chat history across ALL users.

**Request (query params, exactly one mode required):**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| date | string | No* | Single date `YYYY-MM-DD` |
| start_date | string | No* | Range start (*both required for range) |
| end_date | string | No* | Range end (*both required for range) |
| days | number | No* | Last N days (1–365) |
| limit | number | No (default 200) | Max messages to return (1–5000) |

**Example requests:**
```
GET /chat/history/global?date=2026-02-22
GET /chat/history/global?start_date=2026-02-01&end_date=2026-02-22
GET /chat/history/global?days=7&limit=500
```

**Validation:** Returns 400 if zero or multiple modes provided.

**DynamoDB:** Queries GSI `date-timestamp-index` per date bucket, paginates, aggregates up to limit.

### Response Format (both history endpoints)

**Response body:**
```json
{
  "count": 20,
  "messages": [
    {
      "user_id": "user-123",
      "timestamp": 1769747099,
      "created_at": "2026-02-22T10:04:59+00:00",
      "user_message": "What is high blood pressure?",
      "bot_message": "High blood pressure, also known as hypertension..."
    }
  ]
}
```

Messages are always sorted ascending by timestamp.

---

## Error Handling

- **RAG Failures**: Logged as warnings, non-blocking, triggers LLM fallback
- **DynamoDB Errors**: Logged with full traceback, raises 500 error
- **OpenAI Errors**: Logged with full traceback, raises 503 error
- **Validation Errors**: Returns 400 with descriptive message
- **CORS Errors**: Custom handler preserves CORS headers on exceptions

## Logging

All operations are logged using structured logging:
- Info: Normal operations (RAG calls, message storage)
- Warning: RAG failures, non-critical issues
- Error: Exceptions, API failures, storage failures
