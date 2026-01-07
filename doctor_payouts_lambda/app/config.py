import os
import boto3

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

# DynamoDB tables
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
DOCTOR_PAYOUTS_TABLE = dynamodb.Table(os.environ.get("DOCTOR_PAYOUTS_TABLE", "DoctorPayoutsTable"))
DOCTOR_EARNINGS_LEDGER_TABLE = dynamodb.Table(os.environ.get("DOCTOR_EARNINGS_LEDGER_TABLE", "DoctorEarningsLedger"))

