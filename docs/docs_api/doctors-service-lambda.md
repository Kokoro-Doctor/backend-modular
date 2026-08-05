# Doctors Service Lambda

### POST `/doctorsService/setSlots`

Set availability slots for a doctor for the next X days starting from today. Creates the same slots for each day.

```json
{
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "days": 7,
  "slots": [
    {
      "start": "09:00",
      "end": "09:30"
    },
    {
      "start": "10:00",
      "end": "10:30"
    },
    {
      "start": "11:00",
      "end": "11:30"
    },
    {
      "start": "14:00",
      "end": "14:30"
    },
    {
      "start": "15:00",
      "end": "15:30"
    }
  ]
}
```

**Minimal (defaults to 7 days):**

```json
{
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "slots": [
    {
      "start": "09:00",
      "end": "09:30"
    },
    {
      "start": "10:00",
      "end": "10:30"
    }
  ]
}
```

**Response:**

```json
{
  "message": "Availability set successfully for 7 days.",
  "days": 7,
  "slots_per_day": 5,
  "total_slots_created": 35
}
```

**Note:**

- `days` defaults to `7` if not provided. Must be between 1 and 90.
- Slots are created starting from today (UTC).
- The same slots are created for each day.
- Existing slots for the same dates will be overwritten.

---

### POST `/doctorsService/updateSlot`

Update a specific slot's availability.

```json
{
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "date": "2025-01-15",
  "slot_time": "10:00",
  "available": false
}
```

**Enable slot:**

```json
{
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "date": "2025-01-15",
  "slot_time": "10:00",
  "available": true
}
```

---

### DELETE `/doctorsService/doctors/{doctor_id}/slots`

Clear all availability slots for a doctor. This will delete all slots regardless of date or availability status.

**Path Parameters:**

- `doctor_id`: Doctor identifier

**Example:**

```
DELETE /doctorsService/doctors/DOC_12345678-1234-1234-1234-123456789012/slots
```

**Response:**

```json
{
  "message": "All slots cleared successfully for doctor DOC_12345678-1234-1234-1234-123456789012.",
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "slots_deleted": 35
}
```

**Note:**

- This operation is irreversible. All slots for the doctor will be permanently deleted.
- The endpoint verifies the doctor exists before clearing slots.
- Returns the number of slots that were deleted.

---

### POST `/doctorsService/updateProfile`

Update doctor profile with optional documents.

**Without documents:**

```json
{
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "description": "Experienced cardiologist with 15 years of practice",
  "specialization": "Cardiology",
  "experience": "15",
  "fees": 500,
  "timings": "9:00 AM - 6:00 PM",
  "licenseNumber": "MED123456",
  "registrationId": "REG789012",
  "affiliation": "ABC Hospital"
}
```

**With documents (base64 encoded):**

```json
{
  "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
  "description": "Experienced cardiologist",
  "specialization": "Cardiology",
  "degreeCertificate": {
    "filename": "degree.pdf",
    "base64_content": "JVBERi0xLjQKJeLjz9MKMy..."
  },
  "govtIdProof": {
    "filename": "aadhaar.jpg",
    "base64_content": "/9j/4AAQSkZJRgABAQAAAQ..."
  },
  "profilePhoto": {
    "filename": "photo.jpg",
    "base64_content": "/9j/4AAQSkZJRgABAQAAAQ..."
  }
}
```

---

### GET `/doctorsService/doctors`

Fetch doctors list, optionally filtered by category or hospital.

**Query Parameters (optional):**

- `category`: Filter by category (e.g., "Cardiologist")
- `hospital_id`: Filter by hospital ID

**Examples:**

```
GET /doctorsService/doctors
```

```
GET /doctorsService/doctors?category=Cardiologist
```

```
GET /doctorsService/doctors?hospital_id=HOSP_12345
```

---

### GET `/doctorsService/doctor/{doctor_id}`

Get a single doctor by doctor_id.

**Path Parameters:**

- `doctor_id`: Doctor identifier

**Example:**

```
GET /doctorsService/doctor/DOC_12345678-1234-1234-1234-123456789012
```

**Response:**

```json
{
  "doctor": {
    "doctor_id": "DOC_12345678-1234-1234-1234-123456789012",
    "name": "Dr. Jane Smith",
    "specialization": "Cardiologist",
    "profilePhoto": "https://signed-url...",
    ...
  }
}
```

**Note:** Returns presigned URLs for S3 fields (profilePhoto, degreeCertificate, govtIdProof).

---

> **Note:** The `/doctorsService/subscribe` endpoint has been removed. Subscriptions are now managed via the Booking Lambda (`/booking/subscriptions`) and are created automatically after successful payment. See Subscription System documentation.

---
