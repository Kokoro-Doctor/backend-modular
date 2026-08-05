#!/usr/bin/env python3
"""
Doctor Data Update Script

This script reads data from an Excel file and updates existing doctor records in DynamoDB.
It identifies doctors by doctor_id, phoneNumber, or email and updates only the fields provided.

Usage:
    1. Configure .env file with your settings
    2. Prepare Excel file with doctor_id (or phoneNumber/email) and fields to update
    3. Run: python update_doctors.py

Features:
    - Updates existing doctor records
    - Identifies doctors by doctor_id, phoneNumber, or email
    - Only updates fields provided in Excel (leaves others unchanged)
    - Field mapping support (CSV columns to DynamoDB attributes)
    - Batch processing
    - Comprehensive error reporting
"""

import os
import sys
import boto3
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
import pandas as pd
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from boto3.dynamodb.conditions import Key

# Load environment variables from .env file
load_dotenv()


class DoctorUpdater:
    """Doctor data updater for DynamoDB"""
    
    def __init__(self):
        """Initialize updater with configuration from .env"""
        self.load_config()
        self.init_dynamodb()
        self.load_field_mappings()
        
    def load_config(self):
        """Load configuration from environment variables"""
        # AWS Configuration
        self.aws_region = os.getenv('AWS_REGION', 'ap-south-1')
        self.aws_access_key_id = os.getenv('AWS_ACCESS_KEY_ID')
        self.aws_secret_access_key = os.getenv('AWS_SECRET_ACCESS_KEY')
        
        # DynamoDB Configuration
        self.table_name = os.getenv('DYNAMODB_TABLE_NAME', 'Doctors')
        
        # Excel Configuration
        self.excel_file_path = os.getenv('EXCEL_FILE_PATH')
        if not self.excel_file_path:
            raise ValueError("EXCEL_FILE_PATH is required in .env file")
        
        # Identifier Configuration (how to identify doctors)
        # Options: doctor_id, phoneNumber, email
        self.identifier_column = os.getenv('IDENTIFIER_COLUMN', 'doctor_id')
        
        # Update Options
        self.batch_size = int(os.getenv('BATCH_SIZE', '25'))
        self.skip_not_found = os.getenv('SKIP_NOT_FOUND', 'false').lower() == 'true'
        
    def init_dynamodb(self):
        """Initialize DynamoDB connection"""
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
                    excel_col, dynamo_attr = mapping.split(':', 1)
                    self.field_mappings[excel_col.strip()] = dynamo_attr.strip()
    
    def map_field_name(self, excel_column: str) -> str:
        """Map Excel column name to DynamoDB attribute name"""
        return self.field_mappings.get(excel_column, excel_column)
    
    def normalize_phone_number(self, phone: str) -> Optional[str]:
        """Normalize phone number to +91XXXXXXXXXX format"""
        if not phone or pd.isna(phone):
            return None
        
        # Remove all non-digit characters except +
        phone_str = str(phone).strip()
        digits = ''.join(c for c in phone_str if c.isdigit() or c == '+')
        
        if not digits:
            return None
        
        # If starts with +, keep it
        if digits.startswith('+'):
            return digits
        
        # If 10 digits, assume Indian number
        if len(digits) == 10:
            return f"+91{digits}"
        
        # If 12 digits and starts with 91, add +
        if len(digits) == 12 and digits.startswith('91'):
            return f"+{digits}"
        
        # Otherwise return as is
        return digits
    
    def find_doctor_by_identifier(self, identifier_value: str) -> Optional[Dict]:
        """Find doctor by identifier (doctor_id, phoneNumber, or email)"""
        if not identifier_value or pd.isna(identifier_value):
            return None
        
        identifier_value = str(identifier_value).strip()
        
        if self.identifier_column == 'doctor_id':
            # Direct lookup by primary key
            try:
                response = self.table.get_item(Key={"doctor_id": identifier_value})
                return response.get("Item")
            except Exception as e:
                print(f"⚠️  Error looking up doctor_id {identifier_value}: {e}")
                return None
        
        elif self.identifier_column == 'phoneNumber':
            # Lookup via GSI
            normalized_phone = self.normalize_phone_number(identifier_value)
            if not normalized_phone:
                return None
            
            try:
                response = self.table.query(
                    IndexName="phone-index",
                    KeyConditionExpression=Key("phoneNumber").eq(normalized_phone)
                )
                items = response.get("Items", [])
                return items[0] if items else None
            except Exception as e:
                print(f"⚠️  Error looking up phoneNumber {identifier_value}: {e}")
                return None
        
        elif self.identifier_column == 'email':
            # Lookup via GSI (assuming email-index exists)
            email = identifier_value.lower().strip()
            try:
                response = self.table.query(
                    IndexName="email-index",
                    KeyConditionExpression=Key("email").eq(email)
                )
                items = response.get("Items", [])
                return items[0] if items else None
            except Exception as e:
                print(f"⚠️  Error looking up email {identifier_value}: {e}")
                return None
        
        return None
    
    def convert_value(self, value: Any, field_name: str = None) -> Any:
        """Convert pandas value to Python native type"""
        if pd.isna(value):
            return None
        
        # Convert to string first
        str_value = str(value).strip()
        lower_value = str_value.lower()
        
        # Detect boolean values
        if lower_value in ["true", "false"]:
            return lower_value == "true"
        if lower_value in ["yes", "no"]:
            return lower_value == "yes"
        
        # Fields that should always be strings
        string_fields = ['phoneNumber', 'email', 'doctor_id', 'doctorname', 'name',
                        'description', 'location', 'specialization', 'experience',
                        'timings', 'affiliation', 'licenseNumber', 'registrationId']
        
        if field_name and any(field in field_name.lower() for field in string_fields):
            return str_value
        
        # Try to convert to int (for small numbers like fees, experience)
        try:
            if '.' not in str_value:
                return int(str_value)
        except ValueError:
            pass
        
        # Try to convert to float
        try:
            float_val = float(str_value)
            if float_val.is_integer():
                return int(float_val)
            return float_val
        except ValueError:
            pass
        
        # Return as string
        return str_value
    
    def build_update_expression(self, updates: Dict[str, Any]) -> tuple:
        """Build DynamoDB UpdateExpression and ExpressionAttributeValues"""
        if not updates:
            return None, {}, {}
        
        set_parts = []
        remove_parts = []
        expr_values = {}
        expr_names = {}
        
        for i, (field_name, value) in enumerate(updates.items()):
            # Handle special field names that might be reserved words
            placeholder = f"#attr{i}"
            expr_names[placeholder] = field_name
            
            if value is None:
                # Remove attribute
                remove_parts.append(placeholder)
            else:
                # Set attribute
                value_placeholder = f":val{i}"
                expr_values[value_placeholder] = value
                set_parts.append(f"{placeholder} = {value_placeholder}")
        
        # Build update expression parts
        update_expr_parts = []
        
        if set_parts:
            update_expr_parts.append(f"SET {', '.join(set_parts)}")
        
        if remove_parts:
            update_expr_parts.append(f"REMOVE {', '.join(remove_parts)}")
        
        if not update_expr_parts:
            return None, {}, {}
        
        update_expression = " ".join(update_expr_parts)
        return update_expression, expr_values, expr_names
    
    def update_doctor(self, doctor_id: str, updates: Dict[str, Any]) -> bool:
        """Update a doctor record in DynamoDB"""
        if not updates:
            return False
        
        try:
            update_expr, expr_values, expr_names = self.build_update_expression(updates)
            
            if not update_expr:
                return False
            
            update_params = {
                'Key': {'doctor_id': doctor_id},
                'UpdateExpression': update_expr,
                'ExpressionAttributeValues': expr_values,
                'ReturnValues': 'UPDATED_NEW'
            }
            
            if expr_names:
                update_params['ExpressionAttributeNames'] = expr_names
            
            self.table.update_item(**update_params)
            return True
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            raise Exception(f"DynamoDB error ({error_code}): {str(e)}")
        except Exception as e:
            raise Exception(f"Update error: {str(e)}")
    
    def process_row(self, row: Dict, identifier_value: str) -> Optional[Dict]:
        """Process a single Excel row and return updates"""
        # Find the doctor
        doctor = self.find_doctor_by_identifier(identifier_value)
        
        if not doctor:
            return None
        
        doctor_id = doctor.get('doctor_id')
        if not doctor_id:
            return None
        
        # Build updates dictionary
        updates = {}
        
        # Fields to skip (identifier fields, primary keys, etc.)
        skip_fields = [self.identifier_column, 'doctor_id', 'createdAt']
        
        # Process each column
        for excel_column, value in row.items():
            # Skip identifier column
            if excel_column == self.identifier_column:
                continue
            
            # Skip empty values
            if pd.isna(value) or (isinstance(value, str) and value.strip().lower() in ['null', 'none', '']):
                continue
            
            # Map field name
            dynamo_attr = self.map_field_name(excel_column)
            
            # Skip if it's a field we shouldn't update
            if dynamo_attr in skip_fields:
                continue
            
            # Convert value
            converted_value = self.convert_value(value, field_name=dynamo_attr)
            
            # Special handling for phoneNumber - normalize it
            if dynamo_attr == 'phoneNumber':
                normalized = self.normalize_phone_number(value)
                if normalized:
                    converted_value = normalized
            
            # Special handling for email - lowercase it
            if dynamo_attr == 'email':
                converted_value = str(converted_value).lower().strip()
            
            updates[dynamo_attr] = converted_value
        
        return {
            'doctor_id': doctor_id,
            'updates': updates
        }
    
    def read_excel(self) -> pd.DataFrame:
        """Read Excel file"""
        print(f"Reading file: {self.excel_file_path}")
        
        if not os.path.exists(self.excel_file_path):
            raise FileNotFoundError(f"File not found: {self.excel_file_path}")
        
        try:
            df = pd.read_excel(self.excel_file_path)
            print(f"✅ Loaded {len(df)} rows from Excel file")
            print(f"Columns: {', '.join(df.columns.tolist())}")
            return df
        except Exception as e:
            raise Exception(f"Failed to read file: {str(e)}")
    
    def update_data(self) -> Dict:
        """Update doctor data from Excel"""
        # Read Excel
        df = self.read_excel()
        
        # Statistics
        stats = {
            'total_rows': len(df),
            'updated': 0,
            'failed': 0,
            'not_found': 0,
            'skipped': 0,
            'errors': []
        }
        
        # Process each row
        for idx, row in df.iterrows():
            row_num = idx + 2  # +2 for header and 0-based index
            
            try:
                # Get identifier value
                identifier_value = row.get(self.identifier_column)
                if pd.isna(identifier_value) or not identifier_value:
                    stats['skipped'] += 1
                    error_msg = f"Row {row_num}: Missing identifier '{self.identifier_column}'"
                    stats['errors'].append(error_msg)
                    print(f"⏭️  {error_msg}")
                    continue
                
                # Process row
                result = self.process_row(row.to_dict(), str(identifier_value))
                
                if not result:
                    if self.skip_not_found:
                        stats['skipped'] += 1
                        print(f"⏭️  Row {row_num}: Doctor not found (identifier: {identifier_value})")
                    else:
                        stats['not_found'] += 1
                        error_msg = f"Row {row_num}: Doctor not found (identifier: {identifier_value})"
                        stats['errors'].append(error_msg)
                        print(f"❌ {error_msg}")
                    continue
                
                # Update doctor
                doctor_id = result['doctor_id']
                updates = result['updates']
                
                if not updates:
                    stats['skipped'] += 1
                    print(f"⏭️  Row {row_num}: No fields to update")
                    continue
                
                self.update_doctor(doctor_id, updates)
                stats['updated'] += 1
                
                # Print success message
                updated_fields = ', '.join(updates.keys())
                print(f"✅ Row {row_num}: Updated doctor {doctor_id} ({updated_fields})")
                
            except Exception as e:
                stats['failed'] += 1
                error_msg = f"Row {row_num}: Error - {str(e)}"
                stats['errors'].append(error_msg)
                print(f"❌ {error_msg}")
        
        return stats


def main():
    """Main function"""
    try:
        print("=" * 60)
        print("Doctor Data Updater")
        print("=" * 60)
        
        # Check if .env file exists
        if not os.path.exists('.env'):
            print("❌ Error: .env file not found!")
            print("   Please create .env file with required configuration.")
            sys.exit(1)
        
        # Initialize updater
        updater = DoctorUpdater()
        
        # Print configuration
        print(f"Table Name: {updater.table_name}")
        print(f"Excel File: {updater.excel_file_path}")
        print(f"Identifier Column: {updater.identifier_column}")
        print(f"Skip Not Found: {updater.skip_not_found}")
        print("=" * 60)
        print()
        
        # Update data
        stats = updater.update_data()
        
        # Print summary
        print()
        print("=" * 60)
        print("Update Summary")
        print("=" * 60)
        print(f"Total Rows: {stats['total_rows']}")
        print(f"✅ Successfully updated: {stats['updated']}")
        print(f"⏭️  Skipped: {stats['skipped']}")
        print(f"❌ Not Found: {stats['not_found']}")
        print(f"❌ Failed: {stats['failed']}")
        print("=" * 60)
        
        if stats['errors']:
            print("\nErrors:")
            for error in stats['errors'][:10]:  # Show first 10 errors
                print(f"  - {error}")
            if len(stats['errors']) > 10:
                print(f"  ... and {len(stats['errors']) - 10} more errors")
        
        # Exit with appropriate code
        if stats['failed'] > 0:
            sys.exit(1)
        elif stats['updated'] == 0 and stats['not_found'] > 0:
            sys.exit(1)
        else:
            sys.exit(0)
            
    except Exception as e:
        print(f"❌ Fatal Error: {str(e)}")
        sys.exit(1)


if __name__ == '__main__':
    main()

