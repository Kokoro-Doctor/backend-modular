# Chat Lambda

### POST `/chat/send`

Send a chat message and get AI response.

**With user_id:**

```json
{
  "user_id": "USR_12345678-1234-1234-1234-123456789012",
  "message": "What are the symptoms of high blood pressure?",
  "language": "en",
  "role": "patient"
}
```

**With session_id (anonymous user):**

```json
{
  "session_id": "session_abc123",
  "message": "How can I improve my heart health?",
  "language": "hi",
  "role": "patient",
  "chat_count": 3
}
```

**With doctor_id:**

```json
{
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "message": "Tell me about cholesterol management",
  "language": "en",
  "role": "doctor"
}
```

**Response:**

```json
{
  "text": "High blood pressure, also known as hypertension...",
  "is_preview": false
}
```

**Note:**

- Either `user_id`, `session_id`, or `doctor_id` is required.
- `language` defaults to `"en"` if not provided. Options: `en`, `hi`, `es`, `te`
- `role` defaults to `"patient"` if not provided. Options: `"patient"` or `"doctor"`
- `chat_count` is optional, used for anonymous user tracking

---

### GET `/chat/history/user`

Get chat history for a single user. Query params only (no body).

**Query params:**

- `identifier` (required) — user/session/doctor id
- `date` (optional) — single date `YYYY-MM-DD`
- `start_date` + `end_date` (optional) — date range (both required)
- `limit` (optional, default 50, max 500)

**Examples:**

```
GET /chat/history/user?identifier=USR_12345678-1234-1234-1234-123456789012&limit=20
```

```
GET /chat/history/user?identifier=USR_12345678-1234-1234-1234-123456789012&date=2026-02-22
```

```
GET /chat/history/user?identifier=USR_12345678-1234-1234-1234-123456789012&start_date=2026-02-01&end_date=2026-02-22
```

**Response:**

```json
{
  "count": 20,
  "messages": [
    {
      "user_id": "USR_12345678-1234-1234-1234-123456789012",
      "timestamp": 1769747099,
      "created_at": "2026-02-22T10:04:59+00:00",
      "user_message": "What is high blood pressure?",
      "bot_message": "High blood pressure, also known as hypertension..."
    }
  ]
}
```

**Note:** Cannot combine `date` with a date range. Both `start_date` and `end_date` required for range.

---

### GET `/chat/history/global`

Get chat history across ALL users. Query params only (no body). Exactly one mode required: `date`, date range, or `days`.

**Query params:**

- `date` (optional) — single date `YYYY-MM-DD`
- `start_date` + `end_date` (optional) — date range (both required)
- `days` (optional) — last N days (1–365)
- `limit` (optional, default 200, max 5000)

**Examples:**

```
GET /chat/history/global?date=2026-02-22
```

```
GET /chat/history/global?start_date=2026-02-01&end_date=2026-02-22
```

```
GET /chat/history/global?days=7&limit=500
```

**Response:** Same format as `/chat/history/user` (see above).

**Note:** Returns 400 if zero or multiple modes provided.

---
