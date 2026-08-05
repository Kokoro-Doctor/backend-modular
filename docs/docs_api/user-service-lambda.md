# User Service Lambda

### GET `/users/{user_id}`

Get a single user by user_id.

**Path Parameters:**

- `user_id`: User identifier

**Example:**

```
GET /users/USR_12345678-1234-1234-1234-123456789012
```

**Response:**

```json
{
  "user": {
    "user_id": "USR_12345678-1234-1234-1234-123456789012",
    "name": "John Doe",
    "email": "john.doe@example.com",
    "phoneNumber": "+919587733170",
    ...
  }
}
```

---
