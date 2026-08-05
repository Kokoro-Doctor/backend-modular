#!/usr/bin/env python3
"""
Migration script to normalize phone numbers in DynamoDB tables.

This script scans Users, Doctors, and Auth tables and normalizes all phoneNumber fields
to ensure consistent E.164 format: +91 followed by exactly 10 digits.

Usage:
    python normalize_phone_numbers.py

Environment Variables:
    USERS_TABLE: DynamoDB table name for users
    DOCTORS_TABLE: DynamoDB table name for doctors
    AUTH_TABLE: DynamoDB table name for auth records
    AWS_REGION: AWS region (default: ap-south-1)
"""

import os
import sys
import boto3
import re
from typing import Dict, Optional
from botocore.exceptions import ClientError

# Add parent directory to path to import normalize_phone_number
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../auth_lambda'))
from app.utils.db_utils import normalize_phone_number
from app.logger import get_logger

logger = get_logger(__name__)

# Configuration
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
USERS_TABLE_NAME = os.environ.get("USERS_TABLE", "Users")
DOCTORS_TABLE_NAME = os.environ.get("DOCTORS_TABLE", "Doctors")
AUTH_TABLE_NAME = os.environ.get("AUTH_TABLE", "Auth")

# Initialize DynamoDB
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
users_table = dynamodb.Table(USERS_TABLE_NAME)
doctors_table = dynamodb.Table(DOCTORS_TABLE_NAME)
auth_table = dynamodb.Table(AUTH_TABLE_NAME)


def normalize_table_phone_numbers(table, table_name: str, phone_field: str = "phoneNumber") -> Dict[str, int]:
    """
    Normalize phone numbers in a DynamoDB table.
    
    Args:
        table: DynamoDB table resource
        table_name: Name of the table (for logging)
        phone_field: Name of the phone number field to normalize
        
    Returns:
        Dictionary with statistics: {'scanned': int, 'updated': int, 'skipped': int, 'errors': int}
    """
    stats = {
        'scanned': 0,
        'updated': 0,
        'skipped': 0,
        'errors': 0
    }
    
    print(f"\n{'='*60}")
    print(f"Processing {table_name} table...")
    print(f"{'='*60}")
    
    try:
        # Scan the table
        response = table.scan()
        items = response.get('Items', [])
        
        while True:
            for item in items:
                stats['scanned'] += 1
                
                # Get phone number from item
                phone = item.get(phone_field)
                if not phone:
                    stats['skipped'] += 1
                    continue
                
                # Normalize phone number
                normalized = normalize_phone_number(phone)
                
                if not normalized:
                    print(f"  ⚠️  Could not normalize: {phone} (skipping)")
                    stats['skipped'] += 1
                    continue
                
                # If phone number is already normalized, skip
                if normalized == phone:
                    stats['skipped'] += 1
                    continue
                
                # Update the item
                try:
                    # Determine the primary key based on table
                    if table_name == "Users":
                        key = {"user_id": item["user_id"]}
                    elif table_name == "Doctors":
                        key = {"doctor_id": item["doctor_id"]}
                    elif table_name == "Auth":
                        key = {"phoneNumber": phone}  # Current phone is the key
                    else:
                        print(f"  ❌ Unknown table structure: {table_name}")
                        stats['errors'] += 1
                        continue
                    
                    # Update the phone number
                    table.update_item(
                        Key=key,
                        UpdateExpression=f"SET {phone_field} = :phone",
                        ExpressionAttributeValues={":phone": normalized}
                    )
                    
                    print(f"  ✅ Updated: {phone} → {normalized}")
                    stats['updated'] += 1
                    
                except ClientError as e:
                    print(f"  ❌ Error updating {phone}: {e}")
                    stats['errors'] += 1
            
            # Check if there are more items to scan
            if 'LastEvaluatedKey' in response:
                response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
                items = response.get('Items', [])
            else:
                break
                
    except Exception as e:
        print(f"  ❌ Error scanning {table_name}: {e}")
        stats['errors'] += 1
    
    return stats


def main():
    """Main migration function."""
    print("\n" + "="*60)
    print("Phone Number Normalization Migration")
    print("="*60)
    print(f"\nConfiguration:")
    print(f"  AWS Region: {AWS_REGION}")
    print(f"  Users Table: {USERS_TABLE_NAME}")
    print(f"  Doctors Table: {DOCTORS_TABLE_NAME}")
    print(f"  Auth Table: {AUTH_TABLE_NAME}")
    
    # Confirm before proceeding
    response = input("\n⚠️  This will update phone numbers in DynamoDB. Continue? (yes/no): ")
    if response.lower() != 'yes':
        print("Migration cancelled.")
        return
    
    total_stats = {
        'scanned': 0,
        'updated': 0,
        'skipped': 0,
        'errors': 0
    }
    
    # Normalize Users table
    users_stats = normalize_table_phone_numbers(users_table, "Users")
    for key in total_stats:
        total_stats[key] += users_stats[key]
    
    # Normalize Doctors table
    doctors_stats = normalize_table_phone_numbers(doctors_table, "Doctors")
    for key in total_stats:
        total_stats[key] += doctors_stats[key]
    
    # Normalize Auth table (special handling - phoneNumber is the primary key)
    print(f"\n{'='*60}")
    print(f"Processing Auth table (special handling required)...")
    print(f"{'='*60}")
    print("Note: Auth table uses phoneNumber as primary key.")
    print("Records with changed phone numbers will need manual migration.")
    print("This script will only update records where normalization doesn't change the key.")
    
    # For Auth table, we need to be more careful since phoneNumber is the key
    # We'll scan and check if normalization changes the key
    auth_stats = {
        'scanned': 0,
        'updated': 0,
        'skipped': 0,
        'errors': 0,
        'key_changes': 0
    }
    
    try:
        response = auth_table.scan()
        items = response.get('Items', [])
        
        while True:
            for item in items:
                auth_stats['scanned'] += 1
                phone = item.get("phoneNumber")
                if not phone:
                    auth_stats['skipped'] += 1
                    continue
                
                normalized = normalize_phone_number(phone)
                if not normalized:
                    print(f"  ⚠️  Could not normalize: {phone} (skipping)")
                    auth_stats['skipped'] += 1
                    continue
                
                if normalized == phone:
                    auth_stats['skipped'] += 1
                    continue
                
                # If normalization changes the key, we can't update directly
                if normalized != phone:
                    print(f"  ⚠️  Key change required: {phone} → {normalized} (manual migration needed)")
                    auth_stats['key_changes'] += 1
                    # Note: We could create a new item and delete the old one, but that's risky
                    # Better to handle this manually or with a separate script
                    continue
            
            if 'LastEvaluatedKey' in response:
                response = auth_table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
                items = response.get('Items', [])
            else:
                break
    except Exception as e:
        print(f"  ❌ Error scanning Auth table: {e}")
        auth_stats['errors'] += 1
    
    for key in ['scanned', 'updated', 'skipped', 'errors']:
        total_stats[key] += auth_stats[key]
    
    # Print summary
    print(f"\n{'='*60}")
    print("Migration Summary")
    print(f"{'='*60}")
    print(f"\nUsers Table:")
    print(f"  Scanned: {users_stats['scanned']}")
    print(f"  Updated: {users_stats['updated']}")
    print(f"  Skipped: {users_stats['skipped']}")
    print(f"  Errors: {users_stats['errors']}")
    
    print(f"\nDoctors Table:")
    print(f"  Scanned: {doctors_stats['scanned']}")
    print(f"  Updated: {doctors_stats['updated']}")
    print(f"  Skipped: {doctors_stats['skipped']}")
    print(f"  Errors: {doctors_stats['errors']}")
    
    print(f"\nAuth Table:")
    print(f"  Scanned: {auth_stats['scanned']}")
    print(f"  Updated: {auth_stats['updated']}")
    print(f"  Skipped: {auth_stats['skipped']}")
    print(f"  Errors: {auth_stats['errors']}")
    print(f"  Key Changes Required: {auth_stats['key_changes']}")
    
    print(f"\nTotal:")
    print(f"  Scanned: {total_stats['scanned']}")
    print(f"  Updated: {total_stats['updated']}")
    print(f"  Skipped: {total_stats['skipped']}")
    print(f"  Errors: {total_stats['errors']}")
    
    if auth_stats['key_changes'] > 0:
        print(f"\n⚠️  Warning: {auth_stats['key_changes']} Auth records require manual migration")
        print("   because normalization changes the primary key (phoneNumber).")
        print("   Consider running a separate script to handle key changes.")
    
    print("\n✅ Migration completed!")


if __name__ == "__main__":
    main()

