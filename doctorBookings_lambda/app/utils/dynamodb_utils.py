from app.config import AVAILABILITY_TABLE, BOOKING_TABLE
from boto3.dynamodb.conditions import Key

def query_availability(pk: str):
    return AVAILABILITY_TABLE.query(KeyConditionExpression=Key("PK").eq(pk))

def query_booking(pk: str, starts_with: str = None):
    expr = Key("PK").eq(pk)
    if starts_with:
        expr &= Key("SK").begins_with(starts_with)
    return BOOKING_TABLE.query(KeyConditionExpression=expr)

def put_booking_item(item: dict):
    BOOKING_TABLE.put_item(Item=item)

def delete_booking(pk: str, sk: str):
    BOOKING_TABLE.delete_item(Key={"PK": pk, "SK": sk})

def update_availability(pk: str, sk: str, available: bool):
    AVAILABILITY_TABLE.put_item(Item={"PK": pk, "SK": sk, "available": available})
