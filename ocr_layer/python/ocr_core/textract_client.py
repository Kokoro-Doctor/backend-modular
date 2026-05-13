"""
Lazy singleton Textract boto3 client.

Reuses the same client across warm Lambda invocations.
"""
import os
import boto3

_client = None


def get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "textract",
            region_name=os.environ.get("AWS_REGION", "ap-south-1"),
        )
    return _client
