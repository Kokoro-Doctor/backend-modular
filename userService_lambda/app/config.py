import os
import boto3
# from dotenv import load_dotenv
# load_dotenv()

# Environment variables (required ones left as KeyError to fail fast in prod)
USERS_TABLE = os.environ["USERS_TABLE"]

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

# DynamoDB resource & table handles
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
users_table = dynamodb.Table(USERS_TABLE)

