# Phone Number Normalization Migration Script

## Problem

Phone numbers in the `Users` and `Doctors` DynamoDB tables were stored inconsistently:

- Some records: `+918533053387` (correct - 12 digits: +91 + 10 digits)
- Some records: `+9140282197` (incorrect - 11 digits: +91 + 9 digits)
- Some records: `9140282197` (incorrect - missing + prefix)
- Some records: `8533053387` (incorrect - missing +91 prefix)

This inconsistency caused:
- Bugs in login/OTP/lookup functionality
- Difficulty querying users by phone number
- Data integrity issues

## Root Cause

The old `normalize_phone_number` function had logic flaws:
1. If a number already started with `+`, it was returned as-is without validation
2. If a 10-digit number started with `91`, the code would still add `+91`, creating invalid 13-digit numbers
3. Inconsistent handling of edge cases

## Solution

Updated `normalize_phone_number` function in:
- `backend/auth_lambda/app/utils/db_utils.py`
- `backend/userService_lambda/app/utils/db_utils.py`

The new function:
1. Always normalizes to E.164 format: `+91` followed by exactly 10 digits
2. Validates numbers with `+` prefix instead of returning them as-is
3. Handles edge cases consistently:
   - 10-digit numbers → adds `+91` prefix
   - 12-digit numbers starting with `91` → adds `+` prefix
   - 11-digit numbers starting with `91` → extracts last 10 digits and adds `+91`
   - Numbers with `+` prefix → validates and normalizes properly

## Migration Script

Run the migration script to normalize existing phone numbers in DynamoDB:

```bash
cd backend/scripts/normalize_phone_numbers
python normalize_phone_numbers.py
```

The script will:
1. Scan the `Users` table and normalize all `phoneNumber` fields
2. Scan the `Doctors` table and normalize all `phoneNumber` fields
3. Scan the `Auth` table and normalize all `phoneNumber` fields
4. Report statistics on how many records were updated

## Testing

After running the migration, verify:
1. All phone numbers are in format `+91XXXXXXXXXX` (12 characters total)
2. Login/OTP functionality works correctly
3. User/doctor lookups by phone number work correctly

## Notes

- The migration script uses the same `normalize_phone_number` function as the application code
- Records that cannot be normalized will be logged but not updated
- The script is idempotent - safe to run multiple times

