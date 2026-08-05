# Scripts

This directory contains utility scripts for managing the Kokoro Doctor backend.

## set_doctor_availability.py

Sets availability slots for all doctors in the Doctors table for the next N days.

### Features

- Scans all doctors from the Doctors table
- Sets availability slots for each doctor for the next 7 days (configurable)
- Uses default time slots (configurable)
- Option to overwrite existing slots or skip them
- Handles pagination for large doctor lists
- Preserves existing bookings (won't overwrite slots with bookings)

### Usage

#### Basic Usage (7 days, default time slots)

```bash
python scripts/set.py
```

#### Custom Number of Days

```bash
python scripts/set.py --days 30
```

#### Overwrite Existing Slots

```bash
python scripts/set.py --overwrite
```

#### Custom Time Slots

```bash
python scripts/set.py --slots 09:00 10:00 11:00 14:00 15:00 16:00
```

#### Custom Table Names and Region

```bash
python scripts/set.py \
  --region us-east-1 \
  --doctors-table MyDoctorsTable \
  --availability-table MyAvailabilityTable
```

### Command Line Arguments

- `--days`: Number of days ahead to set slots (default: 7)
- `--overwrite`: Overwrite existing slots (default: False, only creates new slots)
- `--region`: AWS region (default: ap-south-1 or AWS_REGION env var)
- `--doctors-table`: Doctors table name (default: Doctors or DOCTORS_TABLE env var)
- `--availability-table`: Availability table name (default: DoctorAvailabilityTable or DOCTOR_AVAILABILITY_TABLE env var)
- `--slots`: Space-separated list of time slots in HH:MM format (default: 09:00 10:00 11:00 14:00 15:00 16:00 17:00)

### Environment Variables

You can also configure the script using environment variables:

- `AWS_REGION`: AWS region (default: ap-south-1)
- `DOCTORS_TABLE`: Name of the Doctors table (default: Doctors)
- `DOCTOR_AVAILABILITY_TABLE`: Name of the availability table (default: DoctorAvailabilityTable)

### Default Time Slots

The script uses the following default time slots:

- Morning: 09:00, 10:00, 11:00
- Afternoon: 14:00, 15:00, 16:00, 17:00

### Schema

The script creates availability entries with the following structure:

- **PK**: `doctor_id`
- **SK**: `date#slot_time` (e.g., `"2025-11-28#10:00"`)
- **Attributes**:
  - `available`: `true`
  - `user_id`: `null`
  - `booking_id`: `null`
  - `created_at`: ISO timestamp

### Example Output

```
============================================================
Doctor Availability Slot Setter
============================================================
AWS Region: ap-south-1
Doctors Table: Doctors
Availability Table: DoctorAvailabilityTable
Days ahead: 7
Time slots: 09:00, 10:00, 11:00, 14:00, 15:00, 16:00, 17:00
Overwrite existing: False
============================================================
Found 5 doctors
Generating slots for dates: 2025-01-15 to 2025-01-29

[1/5] Processing doctor: Dr. John Doe (john@example.com)
  ✓ Created/updated 105 slots
[2/5] Processing doctor: Dr. Jane Smith (jane@example.com)
  ✓ Created/updated 105 slots
...

============================================================
Summary
============================================================
Total doctors processed: 5
Successful: 5
Failed: 0
Total slots created/updated: 525
Expected slots per doctor: 105
============================================================
```

### Notes

- The script preserves existing bookings. If a slot already has a `user_id` or `booking_id`, it will be skipped unless `--overwrite` is used.
- The script uses DynamoDB's `scan` operation to get all doctors, which may take time for large tables.
- Slots are created with `available: true` and can be booked immediately.
