# Doctor Data Update Script

A script to update existing doctor records in DynamoDB from an Excel file.

## Features

- ✅ Updates existing doctor records
- ✅ Identifies doctors by `doctor_id`, `phoneNumber`, or `email`
- ✅ Only updates fields provided in Excel (leaves others unchanged)
- ✅ Field mapping support (Excel columns to DynamoDB attributes)
- ✅ Batch processing
- ✅ Comprehensive error reporting

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure .env File

Create a `.env` file in the `update_doctors` directory:

```env
# AWS Configuration
AWS_REGION=ap-south-1
# AWS credentials are OPTIONAL if already configured via 'aws configure'
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=

# DynamoDB Table
DYNAMODB_TABLE_NAME=Doctors

# Excel File
EXCEL_FILE_PATH=doctors_update.xlsx

# Identifier Column (how to identify doctors)
# Options: doctor_id, phoneNumber, email
IDENTIFIER_COLUMN=doctor_id

# Update Options
SKIP_NOT_FOUND=false
BATCH_SIZE=25

# Field Mappings (Excel column : DynamoDB attribute)
# Example: FIELD_MAPPINGS=full_name:doctorname,mobile:phoneNumber
FIELD_MAPPINGS=
```

### 3. Prepare Excel File

Your Excel file should have:

- One column for identifying the doctor (`doctor_id`, `phoneNumber`, or `email` - as specified in `IDENTIFIER_COLUMN`)
- Columns for the fields you want to update

**Example Excel structure:**

| doctor_id  | doctorname     | specialization | experience | fees | description              | location |
| ---------- | -------------- | -------------- | ---------- | ---- | ------------------------ | -------- |
| DOC_123... | Dr. John Doe   | Cardiology     | 10         | 500  | Experienced cardiologist | Mumbai   |
| DOC_456... | Dr. Jane Smith | Pediatrics     | 5          | 300  | Pediatric specialist     | Delhi    |

### 4. Run Update

```bash
python update_doctors.py
```

## Configuration Details

### Identifier Column

The script identifies doctors using one of these methods:

1. **doctor_id** (default): Direct lookup by primary key

   ```env
   IDENTIFIER_COLUMN=doctor_id
   ```

2. **phoneNumber**: Lookup via phone-index GSI

   ```env
   IDENTIFIER_COLUMN=phoneNumber
   ```

   Phone numbers are automatically normalized to `+91XXXXXXXXXX` format.

3. **email**: Lookup via email-index GSI
   ```env
   IDENTIFIER_COLUMN=email
   ```
   Emails are automatically lowercased.

### Field Mappings

Map Excel column names to DynamoDB attribute names:

```env
FIELD_MAPPINGS=full_name:doctorname,mobile:phoneNumber,email_address:email
```

**Example:**

- Excel has column `full_name` → Maps to DynamoDB attribute `doctorname`
- Excel has column `mobile` → Maps to DynamoDB attribute `phoneNumber`

### Updateable Fields

The following fields can be updated:

- `doctorname` - Doctor's name
- `phoneNumber` - Phone number (automatically normalized)
- `email` - Email address (automatically lowercased)
- `specialization` - Medical specialization
- `experience` - Years of experience
- `fees` - Consultation fees
- `description` - Doctor description
- `location` - Location
- `timings` - Available timings
- `licenseNumber` - License number
- `registrationId` - Registration ID
- `affiliation` - Hospital/clinic affiliation
- `onboarded` - Onboarding status (boolean)

**Note:** Fields not included in the Excel file will remain unchanged in the database.

### Skip Not Found

If a doctor is not found:

- `SKIP_NOT_FOUND=false` (default): Reports as error
- `SKIP_NOT_FOUND=true`: Skips silently

## Examples

### Example 1: Update by doctor_id

**.env:**

```env
AWS_REGION=ap-south-1
DYNAMODB_TABLE_NAME=Doctors
EXCEL_FILE_PATH=updates.xlsx
IDENTIFIER_COLUMN=doctor_id
```

**Excel (updates.xlsx):**
| doctor_id | fees | specialization |
|-----------|------|----------------|
| DOC_123... | 600 | Cardiology |
| DOC_456... | 400 | Pediatrics |

### Example 2: Update by phoneNumber

**.env:**

```env
IDENTIFIER_COLUMN=phoneNumber
```

**Excel:**
| phoneNumber | doctorname | fees |
|-------------|------------|------|
| 9876543210 | Dr. John Doe | 500 |
| 9876543211 | Dr. Jane Smith | 400 |

### Example 3: With Field Mapping

**.env:**

```env
IDENTIFIER_COLUMN=doctor_id
FIELD_MAPPINGS=full_name:doctorname,mobile:phoneNumber,specialty:specialization
```

**Excel:**
| doctor_id | full_name | mobile | specialty |
|-----------|-----------|--------|-----------|
| DOC_123... | Dr. John Doe | 9876543210 | Cardiology |

## Output

The script provides real-time feedback:

```
============================================================
Doctor Data Updater
============================================================
Table Name: Doctors
Excel File: doctors_update.xlsx
Identifier Column: doctor_id
Skip Not Found: False
============================================================

Reading file: doctors_update.xlsx
✅ Loaded 10 rows from Excel file
Columns: doctor_id, doctorname, fees, specialization
✅ Row 2: Updated doctor DOC_123... (fees, specialization)
✅ Row 3: Updated doctor DOC_456... (doctorname, fees)
...

============================================================
Update Summary
============================================================
Total Rows: 10
✅ Successfully updated: 10
⏭️  Skipped: 0
❌ Not Found: 0
❌ Failed: 0
============================================================
```

## Error Handling

- **Doctor Not Found**: Doctor with given identifier doesn't exist
- **Missing Identifier**: Identifier column is empty or missing
- **DynamoDB Errors**: Table not found, permission errors, etc.
- **Excel Errors**: File not found, invalid format, etc.

## Troubleshooting

### "Doctor not found" Error

- Verify the identifier value exists in the database
- Check if using correct identifier column (`doctor_id`, `phoneNumber`, or `email`)
- For `phoneNumber`, ensure format matches (script normalizes to `+91XXXXXXXXXX`)
- For `email`, ensure it's lowercase (script lowercases automatically)

### "Table not found" Error

- Check `DYNAMODB_TABLE_NAME` in .env
- Verify table exists in specified AWS region
- Check AWS credentials

### "Excel file not found" Error

- Check `EXCEL_FILE_PATH` in .env
- Use absolute path if needed
- Verify file exists

### No Fields Updated

- Ensure Excel columns match DynamoDB attribute names (or use `FIELD_MAPPINGS`)
- Check that columns contain non-empty values
- Identifier column is excluded from updates automatically

## Best Practices

1. **Backup First**: Backup your DynamoDB table before bulk updates
2. **Test Small**: Test with a few rows first
3. **Validate Data**: Check Excel format before running
4. **Monitor**: Watch CloudWatch metrics during updates
5. **Use doctor_id**: Most reliable identifier (direct primary key lookup)

## Notes

- The script only updates fields provided in Excel - other fields remain unchanged
- Phone numbers are automatically normalized to `+91XXXXXXXXXX` format
- Emails are automatically lowercased
- Empty cells in Excel are skipped (field not updated)
- The script uses DynamoDB `update_item` with `UpdateExpression` for efficient updates
