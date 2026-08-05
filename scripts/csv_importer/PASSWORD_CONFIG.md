# Password Configuration Explained

## Overview

The password hashing configuration has three settings that work together:

1. **PASSWORD_COLUMN** - Which CSV column contains passwords
2. **HASH_PASSWORDS** - Whether to hash passwords or store them plain
3. **BCRYPT_ROUNDS** - How many rounds of hashing (only used if hashing is enabled)

## How It Works

### Case 1: HASH_PASSWORDS = false (Default)

```env
PASSWORD_COLUMN=password
HASH_PASSWORDS=false
BCRYPT_ROUNDS=12
```

**What happens:**

- Passwords are stored **as plain text** in DynamoDB
- `BCRYPT_ROUNDS` is **ignored** (not used)
- ⚠️ **Warning**: This is NOT secure for production use!

**Example:**

```
CSV: password = "doctor@kokoro123"
DynamoDB: password = "doctor@kokoro123" (plain text)
```

### Case 2: HASH_PASSWORDS = true (Recommended)

```env
PASSWORD_COLUMN=password
HASH_PASSWORDS=true
BCRYPT_ROUNDS=12
```

**What happens:**

- Passwords are **hashed** using bcrypt before storing
- `BCRYPT_ROUNDS` determines the complexity of the hash
- ✅ **Secure**: Passwords cannot be recovered from the hash

**Example:**

```
CSV: password = "doctor@kokoro123"
DynamoDB: password = "$2b$12$rX3Z...hashed...string" (bcrypt hash)
```

## BCRYPT_ROUNDS Explained

**BCRYPT_ROUNDS** is **only used** when `HASH_PASSWORDS=true`. It controls:

- **Security level**: Higher rounds = more secure but slower
- **Performance**: Each round doubles the computation time

**Common values:**

- `10`: Fast, acceptable for many use cases
- `12`: **Default** - Good balance of security and speed (recommended)
- `14`: Very secure, slower (good for critical systems)
- `15+`: Very slow, may impact performance

**Note**: If `HASH_PASSWORDS=false`, the `BCRYPT_ROUNDS` value is completely ignored.

## Code Logic

Here's what the script does (simplified):

```python
# For each CSV row:
for csv_column, value in row.items():
    # Check if this is the password column AND hashing is enabled
    if self.hash_passwords and csv_column == self.password_column:
        # Hash the password using BCRYPT_ROUNDS
        item[dynamo_attr] = self.hash_password(str(value))
        # This uses: bcrypt.hashpw(password.encode(), bcrypt.gensalt(self.bcrypt_rounds))
    else:
        # Store as plain text
        item[dynamo_attr] = self.convert_value(value)
```

## Real-World Examples

### Example 1: Import Doctors with Hashed Passwords

**CSV (doctors.csv):**

```csv
doctorname,email,password
Dr. John Doe,john@example.com,doctor@kokoro123
```

**.env:**

```env
PASSWORD_COLUMN=password
HASH_PASSWORDS=true
BCRYPT_ROUNDS=12
```

**Result in DynamoDB:**

```json
{
  "doctorname": "Dr. John Doe",
  "email": "john@example.com",
  "password": "$2b$12$rX3Zk8J9vM2N5pQ7sT1uW.xYzAbCdEfGhIjKlMnOpQrStUvWxYzA"
}
```

### Example 2: Import Doctors with Plain Text Passwords (NOT Recommended)

**CSV (doctors.csv):**

```csv
doctorname,email,password
Dr. John Doe,john@example.com,doctor@kokoro123
```

**.env:**

```env
PASSWORD_COLUMN=password
HASH_PASSWORDS=false
BCRYPT_ROUNDS=12  # This is ignored!
```

**Result in DynamoDB:**

```json
{
  "doctorname": "Dr. John Doe",
  "email": "john@example.com",
  "password": "doctor@kokoro123" // Plain text - insecure!
}
```

### Example 3: No Password Column in CSV

**CSV (doctors.csv):**

```csv
doctorname,email
Dr. John Doe,john@example.com
```

**.env:**

```env
PASSWORD_COLUMN=password
HASH_PASSWORDS=true
BCRYPT_ROUNDS=12
```

**Result:** No password field is added (password column doesn't exist in CSV)

## Recommendations

### For Production:

```env
HASH_PASSWORDS=true
BCRYPT_ROUNDS=12  # or 10-14 depending on your needs
```

### For Testing/Development:

```env
HASH_PASSWORDS=false  # Only for testing, NOT production!
```

## Summary

| Setting                | Value              | What Happens                                            |
| ---------------------- | ------------------ | ------------------------------------------------------- |
| `HASH_PASSWORDS=false` | Any value          | Passwords stored as plain text, `BCRYPT_ROUNDS` ignored |
| `HASH_PASSWORDS=true`  | `BCRYPT_ROUNDS=10` | Passwords hashed with 10 rounds (fast, less secure)     |
| `HASH_PASSWORDS=true`  | `BCRYPT_ROUNDS=12` | Passwords hashed with 12 rounds (recommended)           |
| `HASH_PASSWORDS=true`  | `BCRYPT_ROUNDS=14` | Passwords hashed with 14 rounds (very secure, slower)   |

**Remember**: `BCRYPT_ROUNDS` only matters when `HASH_PASSWORDS=true`!
