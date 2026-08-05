#!/usr/bin/env python3
"""
Script to set availability slots for all doctors for the next 7 days.

This script:
1. Scans all doctors from the Doctors table
2. For each doctor, sets availability slots for the next 7 days
3. Uses default time slots (configurable)

Usage:
    python set_doctor_availability.py [--days 7] [--overwrite]

Environment Variables:
    AWS_REGION: AWS region (default: ap-south-1)
    DOCTORS_TABLE: Name of the Doctors table (default: Doctors)
    DOCTOR_AVAILABILITY_TABLE: Name of the availability table (default: DoctorAvailabilityTable)
"""

import os
import sys
import boto3
from datetime import datetime, timedelta, time
from typing import List
import argparse
from botocore.exceptions import ClientError

# Default configuration
DEFAULT_AWS_REGION = "ap-south-1"
DEFAULT_DOCTORS_TABLE = "Doctors"
DEFAULT_AVAILABILITY_TABLE = "DoctorAvailabilityTable"
DEFAULT_DAYS_AHEAD = 4  # Number of days ahead to set slots by default

# Default time slots (24-hour format: HH:MM)
DEFAULT_TIME_SLOTS = [
    "09:00", "10:00", "11:00",  # Morning slots
    "14:00", "15:00", "16:00", "17:00"  # Afternoon slots
]


def get_dynamodb_tables(region: str, doctors_table: str, availability_table: str):
    """Initialize DynamoDB resource and get table references."""
    dynamodb = boto3.resource("dynamodb", region_name=region)
    doctors_table_obj = dynamodb.Table(doctors_table)
    availability_table_obj = dynamodb.Table(availability_table)
    return doctors_table_obj, availability_table_obj


def get_all_doctors(doctors_table) -> List[dict]:
    """Scan all doctors from the Doctors table."""
    doctors = []
    try:
        response = doctors_table.scan()
        doctors.extend(response.get("Items", []))
        
        # Handle pagination if there are more items
        while "LastEvaluatedKey" in response:
            response = doctors_table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
            doctors.extend(response.get("Items", []))
        
        print(f"Found {len(doctors)} doctors")
        return doctors
    except ClientError as e:
        print(f"Error scanning doctors table: {e}")
        sys.exit(1)


def generate_dates(days_ahead: int) -> List[str]:
    """Generate list of date strings for the next N days (YYYY-MM-DD format)."""
    dates = []
    today = datetime.utcnow().date()
    for i in range(days_ahead):
        date = today + timedelta(days=i)
        dates.append(date.strftime("%Y-%m-%d"))
    return dates


def get_expiry_timestamp(date_str: str) -> int:
    """
    Calculate expiry timestamp for end of day (23:59:59) in epoch time.
    
    Args:
        date_str: Date in YYYY-MM-DD format
        
    Returns:
        Epoch timestamp (seconds since Unix epoch) for end of day
    """
    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    end_of_day = datetime.combine(date_obj.date(), time(23, 59, 59))
    return int(end_of_day.timestamp())


def set_availability_for_doctor(
    availability_table,
    doctor_id: str,
    dates: List[str],
    time_slots: List[str],
    overwrite: bool = False
) -> int:
    """
    Set availability slots for a doctor for the given dates.
    
    Returns:
        Number of slots created/updated
    """
    created_count = 0
    created_at = datetime.utcnow().isoformat()
    
    for date in dates:
        # Calculate expiry_timestamp for end of day (23:59:59)
        expiry_timestamp = get_expiry_timestamp(date)
        
        for slot_time in time_slots:
            pk = doctor_id
            sk = f"{date}#{slot_time}"
            
            # Check if slot already exists
            if not overwrite:
                try:
                    existing = availability_table.get_item(Key={"PK": pk, "SK": sk})
                    if "Item" in existing:
                        # Skip if slot already exists and has bookings
                        item = existing["Item"]
                        if item.get("user_id") or item.get("booking_id"):
                            continue
                        # Update existing slot to ensure it's available
                        availability_table.put_item(Item={
                            "PK": pk,
                            "SK": sk,
                            "available": True,
                            "user_id": None,
                            "booking_id": None,
                            "created_at": item.get("created_at", created_at),
                            "expiry_timestamp": expiry_timestamp
                        })
                        created_count += 1
                        continue
                except ClientError as e:
                    print(f"  Warning: Error checking existing slot {sk}: {e}")
                    continue
            
            # Create new slot
            try:
                availability_table.put_item(Item={
                    "PK": pk,
                    "SK": sk,
                    "available": True,
                    "user_id": None,
                    "booking_id": None,
                    "created_at": created_at,
                    "expiry_timestamp": expiry_timestamp
                })
                created_count += 1
            except ClientError as e:
                print(f"  Error creating slot {sk}: {e}")
    
    return created_count


def main():
    parser = argparse.ArgumentParser(
        description="Set availability slots for all doctors for the next N days"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS_AHEAD,
        help=f"Number of days ahead to set slots (default: {DEFAULT_DAYS_AHEAD})"
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing slots (default: False, only creates new slots)"
    )
    parser.add_argument(
        "--region",
        type=str,
        default=None,
        help=f"AWS region (default: {DEFAULT_AWS_REGION})"
    )
    parser.add_argument(
        "--doctors-table",
        type=str,
        default=None,
        help=f"Doctors table name (default: {DEFAULT_DOCTORS_TABLE})"
    )
    parser.add_argument(
        "--availability-table",
        type=str,
        default=None,
        help=f"Availability table name (default: {DEFAULT_AVAILABILITY_TABLE})"
    )
    parser.add_argument(
        "--slots",
        type=str,
        nargs="+",
        default=None,
        help="Time slots in HH:MM format (default: 09:00 10:00 11:00 14:00 15:00 16:00 17:00)"
    )
    
    args = parser.parse_args()
    
    # Get configuration from environment or use defaults
    region = args.region or os.environ.get("AWS_REGION", DEFAULT_AWS_REGION)
    doctors_table_name = args.doctors_table or os.environ.get("DOCTORS_TABLE", DEFAULT_DOCTORS_TABLE)
    availability_table_name = args.availability_table or os.environ.get(
        "DOCTOR_AVAILABILITY_TABLE", DEFAULT_AVAILABILITY_TABLE
    )
    time_slots = args.slots or DEFAULT_TIME_SLOTS
    
    print("=" * 60)
    print("Doctor Availability Slot Setter")
    print("=" * 60)
    print(f"AWS Region: {region}")
    print(f"Doctors Table: {doctors_table_name}")
    print(f"Availability Table: {availability_table_name}")
    print(f"Days ahead: {args.days}")
    print(f"Time slots: {', '.join(time_slots)}")
    print(f"Overwrite existing: {args.overwrite}")
    print("=" * 60)
    
    # Initialize DynamoDB
    try:
        doctors_table, availability_table = get_dynamodb_tables(
            region, doctors_table_name, availability_table_name
        )
    except Exception as e:
        print(f"Error initializing DynamoDB: {e}")
        sys.exit(1)
    
    # Get all doctors
    doctors = get_all_doctors(doctors_table)
    if not doctors:
        print("No doctors found. Exiting.")
        sys.exit(0)
    
    # Generate dates
    dates = generate_dates(args.days)
    print(f"Generating slots for dates: {dates[0]} to {dates[-1]}")
    print()
    
    # Process each doctor
    total_slots_created = 0
    successful_doctors = 0
    failed_doctors = 0
    
    for i, doctor in enumerate(doctors, 1):
        doctor_id = doctor.get("doctor_id")
        if not doctor_id:
            print(f"[{i}/{len(doctors)}] Skipping doctor (no doctor_id)")
            failed_doctors += 1
            continue
        
        doctor_name = doctor.get("doctorname", "Unknown")
        doctor_id = doctor.get("doctor_id", "N/A")
        
        print(f"[{i}/{len(doctors)}] Processing doctor: {doctor_name} ({doctor_id})")
        
        try:
            slots_created = set_availability_for_doctor(
                availability_table,
                doctor_id,
                dates,
                time_slots,
                overwrite=args.overwrite
            )
            total_slots_created += slots_created
            successful_doctors += 1
            print(f"  ✓ Created/updated {slots_created} slots")
        except Exception as e:
            print(f"  ✗ Error: {e}")
            failed_doctors += 1
    
    # Summary
    print()
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"Total doctors processed: {len(doctors)}")
    print(f"Successful: {successful_doctors}")
    print(f"Failed: {failed_doctors}")
    print(f"Total slots created/updated: {total_slots_created}")
    print(f"Expected slots per doctor: {len(dates) * len(time_slots)}")
    print("=" * 60)


if __name__ == "__main__":
    main()

