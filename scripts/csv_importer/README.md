# Generic CSV to DynamoDB Importer

A flexible, configuration-driven script to import any CSV data into any DynamoDB table using a `.env` file for configuration.

## Features

- ✅ **Generic**: Works with any CSV and any DynamoDB table
- ✅ **Configuration via .env**: All settings in one place
- ✅ **Field Mapping**: Map CSV columns to DynamoDB attributes
- ✅ **Auto-generated Fields**: UUID, timestamps, etc.
- ✅ **Password Hashing**: Optional bcrypt password hashing
- ✅ **Duplicate Detection**: Check duplicates via GSI
- ✅ **Batch Processing**: Efficient batch writes
- ✅ **Error Handling**: Comprehensive error reporting

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure .env File

```bash
cp .env.example .env
# Edit .env with your settings
```

### 3. Run Import

```bash
python csv_to_dynamodb.py
```

## Configuration (.env File)

### Required Settings

```env
# AWS Configuration
AWS_REGION=ap-south-1
# AWS credentials are OPTIONAL if already configured via 'aws configure'
# Leave empty to use AWS CLI credentials (~/.aws/credentials)
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=

# DynamoDB Table
DYNAMODB_TABLE_NAME=Doctors
PRIMARY_KEY_COLUMN=doctor_id

# CSV File
CSV_FILE_PATH=doctors.csv
```

### Optional Settings

```env
# Import Options
SKIP_DUPLICATES=false
BATCH_SIZE=25
CSV_DELIMITER=,

# Password Hashing
PASSWORD_COLUMN=password
HASH_PASSWORDS=false
BCRYPT_ROUNDS=12

# Field Mappings (CSV column : DynamoDB attribute)
FIELD_MAPPINGS=doctorname:name,phoneNumber:phone

# Auto-generated Fields
AUTO_GENERATED_FIELDS=doctor_id:uuid,createdAt:timestamp

# GSI for Duplicate Checking
GSI_CONFIGURATION=email-index:email,phone-index:phoneNumber

# Skip Columns
SKIP_COLUMNS=old_id,temp_data,notes
```

## Examples

### Example 1: Basic Import

**.env:**

```env
AWS_REGION=ap-south-1
DYNAMODB_TABLE_NAME=Doctors
PRIMARY_KEY_COLUMN=doctor_id
CSV_FILE_PATH=doctors.csv
AUTO_GENERATED_FIELDS=doctor_id:uuid,createdAt:timestamp
```

**CSV (doctors.csv):**

```csv
doctorname,email,phoneNumber,specialization
Dr. John Doe,john@example.com,9876543210,Cardiology
Dr. Jane Smith,jane@example.com,9876543211,Pediatrics
```

### Example 2: With Field Mapping

**.env:**

```env
DYNAMODB_TABLE_NAME=Users
PRIMARY_KEY_COLUMN=user_id
CSV_FILE_PATH=users.csv
FIELD_MAPPINGS=full_name:name,mobile:phoneNumber,email_address:email
AUTO_GENERATED_FIELDS=user_id:uuid,createdAt:timestamp
```

**CSV (users.csv):**

```csv
full_name,mobile,email_address
John Doe,9876543210,john@example.com
```

### Example 3: With Password Hashing

**.env:**

```env
DYNAMODB_TABLE_NAME=Doctors
PRIMARY_KEY_COLUMN=doctor_id
CSV_FILE_PATH=doctors.csv
PASSWORD_COLUMN=password
HASH_PASSWORDS=true
AUTO_GENERATED_FIELDS=doctor_id:uuid,createdAt:timestamp
```

**CSV (doctors.csv):**

```csv
doctorname,email,password
Dr. John Doe,john@example.com,doctor@kokoro123
```

### Example 4: With Duplicate Checking

**.env:**

```env
DYNAMODB_TABLE_NAME=Doctors
PRIMARY_KEY_COLUMN=doctor_id
CSV_FILE_PATH=doctors.csv
GSI_CONFIGURATION=email-index:email,phone-index:phoneNumber
SKIP_DUPLICATES=true
AUTO_GENERATED_FIELDS=doctor_id:uuid,createdAt:timestamp
```

## Field Mapping

Map CSV column names to DynamoDB attribute names:

```env
FIELD_MAPPINGS=old_column_name:new_attribute_name,another_column:another_attribute
```

**Example:**

```env
FIELD_MAPPINGS=doctorname:name,phoneNumber:phone,emailAddress:email
```

## Auto-Generated Fields

Automatically generate values for fields:

**Supported Types:**

- `uuid`: Generates UUID string
- `timestamp`: Generates ISO timestamp
- `iso_timestamp`: Same as timestamp

**Example:**

```env
AUTO_GENERATED_FIELDS=doctor_id:uuid,createdAt:timestamp,updatedAt:timestamp
```

## GSI Configuration

Configure Global Secondary Indexes for duplicate checking:

```env
GSI_CONFIGURATION=index-name:column-name,another-index:another-column
```

**Example:**

```env
GSI_CONFIGURATION=email-index:email,phone-index:phoneNumber
```

## Skip Columns

Skip specific columns from CSV:

```env
SKIP_COLUMNS=old_id,temp_data,notes,internal_notes
```

## Password Hashing

Hash passwords using bcrypt:

```env
PASSWORD_COLUMN=password
HASH_PASSWORDS=true
BCRYPT_ROUNDS=12
```

## AWS Credentials

The script uses boto3's credential chain, which checks in this order:

1. **.env file** (if provided):

   ```env
   AWS_ACCESS_KEY_ID=your_key
   AWS_SECRET_ACCESS_KEY=your_secret
   ```

   **Note**: If you leave these empty in `.env`, the script will use AWS CLI credentials instead.

2. **AWS CLI Configuration** (recommended):

   ```bash
   aws configure
   ```

   If you've already configured AWS credentials via `aws configure`, you can **leave these empty in .env**:

   ```env
   AWS_ACCESS_KEY_ID=
   AWS_SECRET_ACCESS_KEY=
   ```

3. **IAM Role** (if running on EC2/Lambda)

**Recommended**: If you already have credentials via `aws configure`, just set `AWS_REGION` in `.env` and leave the access keys empty. The script will automatically use your AWS CLI credentials from `~/.aws/credentials`.

## Output

The script provides real-time feedback:

```
============================================================
CSV to DynamoDB Importer
============================================================
Table Name: Doctors
CSV File: doctors.csv
Primary Key Column: doctor_id
Skip Duplicates: False
Batch Size: 25
============================================================

Reading CSV file: doctors.csv
✅ Loaded 21 rows from CSV
✅ Row 2: Imported (PK: abc123...)
✅ Row 3: Imported (PK: def456...)
...

============================================================
Import Summary
============================================================
Total Rows: 21
✅ Successfully imported: 21
⏭️  Skipped: 0
❌ Failed: 0
============================================================
```

## Error Handling

- **Validation Errors**: Missing required fields, invalid data types
- **Duplicate Detection**: Checks via GSI before inserting
- **DynamoDB Errors**: Table not found, permission errors, etc.
- **CSV Errors**: File not found, invalid format, etc.

## Troubleshooting

### "Table not found" Error

- Check `DYNAMODB_TABLE_NAME` in .env
- Verify table exists in specified AWS region
- Check AWS credentials

### "CSV file not found" Error

- Check `CSV_FILE_PATH` in .env
- Use absolute path if needed
- Verify file exists

### "Primary key missing" Error

- Check `PRIMARY_KEY_COLUMN` in .env
- Ensure CSV has the primary key column
- Or use `AUTO_GENERATED_FIELDS` to generate it

### Import Fails Silently

- Check AWS credentials
- Verify table permissions
- Check CloudWatch logs

## Best Practices

1. **Test First**: Use `SKIP_DUPLICATES=true` for initial imports
2. **Backup Data**: Backup your DynamoDB table before bulk imports
3. **Validate CSV**: Check CSV format before importing
4. **Monitor**: Watch CloudWatch metrics during import
5. **Batch Size**: Adjust `BATCH_SIZE` based on item size (max 25 items per batch)

## License

This script is provided as-is for internal use.
