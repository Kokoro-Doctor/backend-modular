import os
import boto3
# from dotenv import load_dotenv
# load_dotenv()

DYNAMODB_TABLE = os.getenv("DYNAMODB_TABLE")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
RAG_SERVER_URL = os.getenv("RAG_SERVER_URL")

dynamodb = boto3.resource("dynamodb", region_name="ap-south-1")
chat_table = dynamodb.Table(DYNAMODB_TABLE)
