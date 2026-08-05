#!/usr/bin/env python3
"""
Generic CSV to DynamoDB Importer

This script reads data from a CSV file and imports it into a DynamoDB table.
All configuration is done via .env file.

Usage:
    1. Copy .env.example to .env
    2. Configure .env with your settings
    3. Run: python csv_to_dynamodb.py

Features:
    - Generic CSV to DynamoDB import
    - Configuration via .env file
    - Field mapping (CSV columns to DynamoDB attributes)
    - Auto-generated fields (UUID, timestamps)
    - Password hashing support
    - Duplicate detection via GSI
    - Batch processing
"""

import os
import sys
import uuid
import re
import bcrypt
import boto3
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
import pandas as pd
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class CSVToDynamoDBImporter:
    """Generic CSV to DynamoDB importer"""
    
    def __init__(self):
        """Initialize importer with configuration from .env"""
        self.load_config()
        self.init_dynamodb()
        self.load_field_mappings()
        self.load_auto_generated_fields()
        self.load_skip_columns()
        
    def load_config(self):
        """Load configuration from environment variables"""
        # AWS Configuration
        self.aws_region = os.getenv('AWS_REGION', 'ap-south-1')
        self.aws_access_key_id = os.getenv('AWS_ACCESS_KEY_ID')
        self.aws_secret_access_key = os.getenv('AWS_SECRET_ACCESS_KEY')
        
        # DynamoDB Configuration
        self.table_name = os.getenv('DYNAMODB_TABLE_NAME')
        if not self.table_name:
            raise ValueError("DYNAMODB_TABLE_NAME is required in .env file")
        
        self.primary_key_column = os.getenv('PRIMARY_KEY_COLUMN')
        if not self.primary_key_column:
            raise ValueError("PRIMARY_KEY_COLUMN is required in .env file")
        
        # CSV/Excel Configuration
        self.csv_file_path = os.getenv('CSV_FILE_PATH')
        if not self.csv_file_path:
            raise ValueError("CSV_FILE_PATH is required in .env file")
        
        self.csv_delimiter = os.getenv('CSV_DELIMITER', ',')
        
        # UUID Prefix Configuration (for prefixed UUIDs like DOC_<uuid>)
        self.uuid_prefix = os.getenv('UUID_PREFIX', '')
        
        # Import Options
        self.skip_duplicates = os.getenv('SKIP_DUPLICATES', 'false').lower() == 'true'
        self.batch_size = int(os.getenv('BATCH_SIZE', '25'))
        
        # Password Configuration
        self.password_column = os.getenv('PASSWORD_COLUMN')
        self.hash_passwords = os.getenv('HASH_PASSWORDS', 'false').lower() == 'true'
        self.bcrypt_rounds = int(os.getenv('BCRYPT_ROUNDS', '12'))
        
    def init_dynamodb(self):
        """Initialize DynamoDB connection"""
        # Create boto3 session with credentials if provided
        if self.aws_access_key_id and self.aws_secret_access_key:
            session = boto3.Session(
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key,
                region_name=self.aws_region
            )
            self.dynamodb = session.resource('dynamodb')
        else:
            # Use default credential chain (AWS CLI, IAM role, etc.)
            self.dynamodb = boto3.resource('dynamodb', region_name=self.aws_region)
        
        self.table = self.dynamodb.Table(self.table_name)
        
    def load_field_mappings(self):
        """Load field mappings from environment variable"""
        mappings_str = os.getenv('FIELD_MAPPINGS', '')
        self.field_mappings = {}
        
        if mappings_str:
            for mapping in mappings_str.split(','):
                mapping = mapping.strip()
                if ':' in mapping:
                    csv_col, dynamo_attr = mapping.split(':', 1)
                    self.field_mappings[csv_col.strip()] = dynamo_attr.strip()
        
    def load_auto_generated_fields(self):
        """Load auto-generated field configuration"""
        auto_fields_str = os.getenv('AUTO_GENERATED_FIELDS', '')
        self.auto_generated_fields = {}
        
        if auto_fields_str:
            for field_config in auto_fields_str.split(','):
                field_config = field_config.strip()
                if ':' in field_config:
                    field_name, value_type = field_config.split(':', 1)
                    self.auto_generated_fields[field_name.strip()] = value_type.strip()
        
    def load_skip_columns(self):
        """Load columns to skip from CSV"""
        skip_str = os.getenv('SKIP_COLUMNS', '')
        self.skip_columns = [col.strip() for col in skip_str.split(',') if col.strip()]
        
    def generate_uuid(self) -> str:
        """Generate a UUID string, optionally with prefix"""
        uuid_str = str(uuid.uuid4())
        if self.uuid_prefix:
            return f"{self.uuid_prefix}_{uuid_str}"
        return uuid_str
    
    def _prefixed_id(self, prefix: str) -> str:
        """Generate a prefixed UUID (e.g., DOC_550e8400-e29b-41d4-a716-446655440000)"""
        return f"{prefix}_{str(uuid.uuid4())}"
    
    def generate_timestamp(self) -> str:
        """Generate ISO timestamp"""
        return datetime.now(timezone.utc).isoformat()
    
    def generate_iso_timestamp(self) -> str:
        """Generate ISO timestamp (alias)"""
        return self.generate_timestamp()
    
    def hash_password(self, password: str) -> str:
        """Hash password using bcrypt"""
        if not password:
            return ""
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt(self.bcrypt_rounds)).decode()
    
    def map_field_name(self, csv_column: str) -> str:
        """Map CSV column name to DynamoDB attribute name"""
        return self.field_mappings.get(csv_column, csv_column)
    
    def should_skip_column(self, column: str) -> bool:
        """Check if column should be skipped"""
        return column in self.skip_columns
    
    def process_row(self, row: Dict) -> Dict:
        """Process a single CSV row into DynamoDB item"""
        item = {}
        
        # Fields that should be included even if empty (string fields)
        string_fields_allow_empty = ['experience', 'description', 'specialization', 'location', 
                                     'timings', 'affiliation', 'licenseNumber', 'registrationId']
        
        # Process each column
        for csv_column, value in row.items():
            # Skip if column is in skip list
            if self.should_skip_column(csv_column):
                continue
            
            # Map field name
            dynamo_attr = self.map_field_name(csv_column)
            
            # Check if value is null/empty
            is_empty = pd.isna(value) or (isinstance(value, str) and value.strip().lower() in ['null', 'none', ''])
            
            # Skip null/empty values, EXCEPT for string fields that should be included even if empty
            if is_empty:
                # For string fields that allow empty, include them as empty string
                if any(field in dynamo_attr.lower() for field in string_fields_allow_empty):
                    item[dynamo_attr] = ""
                    continue
                else:
                    # Skip other empty fields
                    continue
            
            # Handle password hashing
            if self.hash_passwords and csv_column == self.password_column:
                item[dynamo_attr] = self.hash_password(str(value))
            else:
                # Convert value to appropriate type (pass field name for smart conversion)
                item[dynamo_attr] = self.convert_value(value, field_name=dynamo_attr)
        
        # Add auto-generated fields
        for field_name, value_type in self.auto_generated_fields.items():
            if field_name not in item:  # Don't override existing values
                if value_type == 'uuid':
                    item[field_name] = self.generate_uuid()
                elif value_type.startswith('prefixed_uuid:'):
                    # Support prefixed UUIDs like prefixed_uuid:DOC
                    prefix = value_type.split(':', 1)[1] if ':' in value_type else self.uuid_prefix or 'DOC'
                    item[field_name] = self._prefixed_id(prefix)
                elif value_type in ['timestamp', 'iso_timestamp']:
                    item[field_name] = self.generate_timestamp()
                else:
                    item[field_name] = value_type  # Use as literal value
        
        return item
    
    def convert_value(self, value: Any, field_name: str = None) -> Any:
        """Convert pandas value to Python native type"""
        if pd.isna(value):
            return None
        
        # Convert to string first
        str_value = str(value).strip()
        lower_value = str_value.lower()
        
        # Detect boolean values expressed as text
        if lower_value in ["true", "false"]:
            return lower_value == "true"
        if lower_value in ["yes", "no"]:
            return lower_value == "yes"
        
        # Fields that should always be strings (GSI keys, IDs, etc.)
        string_fields = ['phoneNumber', 'email', 'phone', 'id', 'doctor_id', 'user_id', 
                        'token_id', 'licenseNumber', 'registrationId', 'category',
                        'doctorname', 'username', 'name', 'description', 'location',
                        'specialization', 'experience', 'timings', 'affiliation']
        
        # Check if this field should be a string
        if field_name and any(field in field_name.lower() for field in string_fields):
            return str_value
        
        # Check if it's a numeric-looking value but might need to stay as string
        # (e.g., phone numbers, IDs that start with 0, etc.)
        if str_value.isdigit():
            # If it looks like a phone number (10+ digits) or starts with +, keep as string
            if len(str_value) >= 10 or str_value.startswith('+'):
                return str_value
            # If it's a small number, check if it should be int
            # But default to string if we're not sure
            if len(str_value) < 10:
                try:
                    return int(str_value)
                except ValueError:
                    return str_value
        
        # Try to convert to int (for small numbers)
        try:
            if '.' not in str_value and len(str_value) < 10:
                return int(str_value)
        except ValueError:
            pass
        
        # Try to convert to float
        try:
            float_val = float(str_value)
            # If it's a whole number and small, return as int
            if float_val.is_integer() and len(str_value) < 10:
                return int(float_val)
            return float_val
        except ValueError:
            pass
        
        # Return as string
        return str_value
    
    def check_duplicate(self, item: Dict) -> Optional[str]:
        """Check if item already exists using GSI"""
        gsi_config = os.getenv('GSI_CONFIGURATION', '')
        if not gsi_config:
            return None
        
        try:
            from boto3.dynamodb.conditions import Key
            
            for gsi_info in gsi_config.split(','):
                gsi_info = gsi_info.strip()
                if ':' not in gsi_info:
                    continue
                
                index_name, column_name = gsi_info.split(':', 1)
                index_name = index_name.strip()
                column_name = column_name.strip()
                
                # Map column name if needed
                dynamo_attr = self.map_field_name(column_name)
                
                if dynamo_attr in item:
                    value = item[dynamo_attr]
                    response = self.table.query(
                        IndexName=index_name,
                        KeyConditionExpression=Key(dynamo_attr).eq(value)
                    )
                    if response.get('Items'):
                        return f"Duplicate found in {index_name} for {dynamo_attr}={value}"
        except Exception as e:
            print(f"⚠️  Warning: Could not check for duplicates: {e}")
        
        return None
    
    def read_csv(self) -> pd.DataFrame:
        """Read CSV or Excel file"""
        print(f"Reading file: {self.csv_file_path}")
        
        if not os.path.exists(self.csv_file_path):
            raise FileNotFoundError(f"File not found: {self.csv_file_path}")
        
        try:
            # Check if it's an Excel file
            if self.csv_file_path.lower().endswith(('.xlsx', '.xls')):
                df = pd.read_excel(self.csv_file_path)
                print(f"✅ Loaded {len(df)} rows from Excel file")
            else:
                df = pd.read_csv(self.csv_file_path, delimiter=self.csv_delimiter)
                print(f"✅ Loaded {len(df)} rows from CSV")
            return df
        except Exception as e:
            raise Exception(f"Failed to read file: {str(e)}")
    
    def import_data(self) -> Dict:
        """Import data from CSV to DynamoDB"""
        # Read CSV
        df = self.read_csv()
        
        # Statistics
        stats = {
            'total_rows': len(df),
            'imported': 0,
            'failed': 0,
            'skipped': 0,
            'errors': []
        }
        
        # Process each row
        items_to_write = []
        
        for idx, row in df.iterrows():
            row_num = idx + 2  # +2 for header and 0-based index
            
            try:
                # Process row
                item = self.process_row(row.to_dict())
                
                # Check for primary key
                primary_key_attr = self.map_field_name(self.primary_key_column)
                if primary_key_attr not in item:
                    # Generate UUID if primary key is missing and configured for auto-generation
                    if primary_key_attr in self.auto_generated_fields:
                        field_type = self.auto_generated_fields[primary_key_attr]
                        if field_type == 'uuid':
                            item[primary_key_attr] = self.generate_uuid()
                        elif field_type.startswith('prefixed_uuid:'):
                            # Support prefixed UUIDs like prefixed_uuid:DOC
                            prefix = field_type.split(':', 1)[1] if ':' in field_type else self.uuid_prefix or 'DOC'
                            item[primary_key_attr] = self._prefixed_id(prefix)
                        elif field_type in ['timestamp', 'iso_timestamp']:
                            item[primary_key_attr] = self.generate_timestamp()
                        else:
                            # Use as literal value
                            item[primary_key_attr] = field_type
                    else:
                        stats['failed'] += 1
                        error_msg = f"Row {row_num}: Primary key '{primary_key_attr}' is missing. " \
                                   f"Add '{primary_key_attr}:uuid' or '{primary_key_attr}:prefixed_uuid:DOC' to AUTO_GENERATED_FIELDS in .env to auto-generate it."
                        stats['errors'].append(error_msg)
                        print(f"❌ {error_msg}")
                        continue
                
                # Check for duplicates
                duplicate_error = self.check_duplicate(item)
                if duplicate_error:
                    if self.skip_duplicates:
                        stats['skipped'] += 1
                        print(f"⏭️  Row {row_num}: Skipped - {duplicate_error}")
                        continue
                    else:
                        stats['failed'] += 1
                        error_msg = f"Row {row_num}: {duplicate_error}"
                        stats['errors'].append(error_msg)
                        print(f"❌ {error_msg}")
                        continue
                
                # Add to batch
                items_to_write.append({
                    'row_num': row_num,
                    'item': item
                })
                
                # Write batch if full
                if len(items_to_write) >= self.batch_size:
                    self.write_batch(items_to_write, stats)
                    items_to_write = []
                
            except Exception as e:
                stats['failed'] += 1
                error_msg = f"Row {row_num}: Error - {str(e)}"
                stats['errors'].append(error_msg)
                print(f"❌ {error_msg}")
        
        # Write remaining items
        if items_to_write:
            self.write_batch(items_to_write, stats)
        
        return stats
    
    def write_batch(self, items: List[Dict], stats: Dict):
        """Write a batch of items to DynamoDB"""
        for item_data in items:
            row_num = item_data['row_num']
            item = item_data['item']
            
            try:
                self.table.put_item(Item=item)
                stats['imported'] += 1
                
                # Print success message
                primary_key_attr = self.map_field_name(self.primary_key_column)
                primary_key_value = item.get(primary_key_attr, 'N/A')
                print(f"✅ Row {row_num}: Imported (PK: {primary_key_value})")
                
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', 'Unknown')
                error_msg = f"Row {row_num}: DynamoDB error ({error_code}): {str(e)}"
                stats['failed'] += 1
                stats['errors'].append(error_msg)
                print(f"❌ {error_msg}")
            except Exception as e:
                error_msg = f"Row {row_num}: Unexpected error: {str(e)}"
                stats['failed'] += 1
                stats['errors'].append(error_msg)
                print(f"❌ {error_msg}")


def main():
    """Main function"""
    try:
        print("=" * 60)
        print("CSV to DynamoDB Importer")
        print("=" * 60)
        
        # Check if .env file exists
        if not os.path.exists('.env'):
            print("❌ Error: .env file not found!")
            print("   Please copy .env.example to .env and configure it.")
            sys.exit(1)
        
        # Initialize importer
        importer = CSVToDynamoDBImporter()
        
        # Print configuration
        print(f"Table Name: {importer.table_name}")
        print(f"CSV File: {importer.csv_file_path}")
        print(f"Primary Key Column: {importer.primary_key_column}")
        print(f"Skip Duplicates: {importer.skip_duplicates}")
        print(f"Batch Size: {importer.batch_size}")
        if importer.hash_passwords:
            print(f"Password Hashing: Enabled (column: {importer.password_column})")
        print("=" * 60)
        print()
        
        # Import data
        stats = importer.import_data()
        
        # Print summary
        print()
        print("=" * 60)
        print("Import Summary")
        print("=" * 60)
        print(f"Total Rows: {stats['total_rows']}")
        print(f"✅ Successfully imported: {stats['imported']}")
        print(f"⏭️  Skipped: {stats['skipped']}")
        print(f"❌ Failed: {stats['failed']}")
        print("=" * 60)
        
        if stats['errors']:
            print("\nErrors:")
            for error in stats['errors'][:10]:  # Show first 10 errors
                print(f"  - {error}")
            if len(stats['errors']) > 10:
                print(f"  ... and {len(stats['errors']) - 10} more errors")
        
        # Exit with appropriate code
        if stats['failed'] > 0 and not importer.skip_duplicates:
            sys.exit(1)
        elif stats['imported'] == 0:
            sys.exit(1)
        else:
            sys.exit(0)
            
    except Exception as e:
        print(f"❌ Fatal Error: {str(e)}")
        sys.exit(1)


if __name__ == '__main__':
    main()

