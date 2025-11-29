"""
Utility functions for deleting user/doctor accounts and all related data.
"""
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError

from app import config
from app.logger import get_logger
from app.utils.db_utils import (
    get_user_by_phone,
    get_doctor_by_phone,
    normalize_phone_number,
)

logger = get_logger(__name__)


def delete_user_account(user_id: str, phone_number: str, email: str = None):
    """Delete all data related to a user account."""
    deleted_items = {
        "users": False,
        "auth": False,
        "tokens": [],
        "sessions": [],
        "chat_history": [],
        "bookings": [],
        "s3_files": [],
    }

    try:
        # 1. Delete from Users table
        try:
            config.users_table.delete_item(Key={"user_id": user_id})
            deleted_items["users"] = True
            logger.info(f"Deleted user {user_id} from Users table")
        except Exception as e:
            logger.error(f"Error deleting user from Users table: {e}")

        # 2. Delete from AuthTable
        normalized_phone = normalize_phone_number(phone_number)
        try:
            config.auth_table.delete_item(Key={"phoneNumber": normalized_phone})
            deleted_items["auth"] = True
            logger.info(f"Deleted auth record for {normalized_phone}")
        except Exception as e:
            logger.error(f"Error deleting auth record: {e}")

        # 3. Delete all auth tokens for this phone/email
        try:
            if normalized_phone:
                response = config.auth_tokens_table.query(
                    IndexName="phone-index",
                    KeyConditionExpression=Key("phoneNumber").eq(normalized_phone)
                )
                for item in response.get("Items", []):
                    config.auth_tokens_table.delete_item(
                        Key={"token_id": item["token_id"], "purpose": item["purpose"]}
                    )
                    deleted_items["tokens"].append(item["token_id"])
            
            if email:
                response = config.auth_tokens_table.query(
                    IndexName="email-index",
                    KeyConditionExpression=Key("email").eq(email.lower())
                )
                for item in response.get("Items", []):
                    config.auth_tokens_table.delete_item(
                        Key={"token_id": item["token_id"], "purpose": item["purpose"]}
                    )
                    deleted_items["tokens"].append(item["token_id"])
            logger.info(f"Deleted {len(deleted_items['tokens'])} auth tokens")
        except Exception as e:
            logger.error(f"Error deleting auth tokens: {e}")

        # 4. Delete chat history
        try:
            response = config.chat_table.query(
                KeyConditionExpression=Key("user_id").eq(user_id)
            )
            for item in response.get("Items", []):
                config.chat_table.delete_item(
                    Key={"user_id": user_id, "timestamp": item["timestamp"]}
                )
                deleted_items["chat_history"].append(item["timestamp"])
            logger.info(f"Deleted {len(deleted_items['chat_history'])} chat history records")
        except Exception as e:
            logger.error(f"Error deleting chat history: {e}")

        # 5. Delete bookings (using GSI)
        try:
            response = config.booking_table.query(
                IndexName="GSI_UserBookings",
                KeyConditionExpression=Key("user_id").eq(user_id)
            )
            for item in response.get("Items", []):
                config.booking_table.delete_item(
                    Key={"PK": item["PK"], "SK": item["SK"]}
                )
                deleted_items["bookings"].append(item["PK"])
            logger.info(f"Deleted {len(deleted_items['bookings'])} bookings")
        except Exception as e:
            logger.error(f"Error deleting bookings: {e}")

        # 6. Delete S3 Medilocker files
        try:
            prefix = f"Medilocker/Users/{user_id}/"
            paginator = config.s3_client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=config.S3_BUCKET, Prefix=prefix)
            
            for page in pages:
                if "Contents" in page:
                    for obj in page["Contents"]:
                        config.s3_client.delete_object(
                            Bucket=config.S3_BUCKET, Key=obj["Key"]
                        )
                        deleted_items["s3_files"].append(obj["Key"])
            logger.info(f"Deleted {len(deleted_items['s3_files'])} S3 files from Medilocker")
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchKey":
                logger.error(f"Error deleting S3 files: {e}")
        except Exception as e:
            logger.error(f"Error deleting S3 files: {e}")

        # 7. Remove user from doctor subscribers lists
        try:
            # Scan doctors table to find subscribers lists containing this user_id
            # Use pagination to handle large tables
            paginator = config.doctors_table.meta.client.get_paginator("scan")
            pages = paginator.paginate(TableName=config.doctors_table.name)
            
            for page in pages:
                for doctor in page.get("Items", []):
                    subscribers = doctor.get("subscribers", [])
                    if user_id in subscribers:
                        subscribers.remove(user_id)
                        config.doctors_table.update_item(
                            Key={"doctor_id": doctor["doctor_id"]},
                            UpdateExpression="SET subscribers = :subs",
                            ExpressionAttributeValues={":subs": subscribers},
                        )
                        logger.info(f"Removed user {user_id} from doctor {doctor['doctor_id']} subscribers")
        except Exception as e:
            logger.error(f"Error removing user from doctor subscribers: {e}")

    except Exception as e:
        logger.error(f"Unexpected error in delete_user_account: {e}", exc_info=True)
        raise

    return deleted_items


def delete_doctor_account(doctor_id: str, phone_number: str, email: str = None):
    """Delete all data related to a doctor account."""
    deleted_items = {
        "doctors": False,
        "auth": False,
        "tokens": [],
        "sessions": [],
        "availability": [],
        "bookings": [],
        "s3_files": [],
    }

    try:
        # 1. Delete from Doctors table
        try:
            config.doctors_table.delete_item(Key={"doctor_id": doctor_id})
            deleted_items["doctors"] = True
            logger.info(f"Deleted doctor {doctor_id} from Doctors table")
        except Exception as e:
            logger.error(f"Error deleting doctor from Doctors table: {e}")

        # 2. Delete from AuthTable
        normalized_phone = normalize_phone_number(phone_number)
        try:
            config.auth_table.delete_item(Key={"phoneNumber": normalized_phone})
            deleted_items["auth"] = True
            logger.info(f"Deleted auth record for {normalized_phone}")
        except Exception as e:
            logger.error(f"Error deleting auth record: {e}")

        # 3. Delete all auth tokens for this phone/email
        try:
            if normalized_phone:
                response = config.auth_tokens_table.query(
                    IndexName="phone-index",
                    KeyConditionExpression=Key("phoneNumber").eq(normalized_phone)
                )
                for item in response.get("Items", []):
                    config.auth_tokens_table.delete_item(
                        Key={"token_id": item["token_id"], "purpose": item["purpose"]}
                    )
                    deleted_items["tokens"].append(item["token_id"])
            
            if email:
                response = config.auth_tokens_table.query(
                    IndexName="email-index",
                    KeyConditionExpression=Key("email").eq(email.lower())
                )
                for item in response.get("Items", []):
                    config.auth_tokens_table.delete_item(
                        Key={"token_id": item["token_id"], "purpose": item["purpose"]}
                    )
                    deleted_items["tokens"].append(item["token_id"])
            logger.info(f"Deleted {len(deleted_items['tokens'])} auth tokens")
        except Exception as e:
            logger.error(f"Error deleting auth tokens: {e}")

        # 4. Delete availability slots
        try:
            # Availability PK format: doctor_id
            # SK format: date#slot_time (e.g., "2025-11-28#10:00")
            response = config.availability_table.query(
                KeyConditionExpression=Key("PK").eq(doctor_id)
            )
            for item in response.get("Items", []):
                config.availability_table.delete_item(
                    Key={"PK": doctor_id, "SK": item["SK"]}
                )
                deleted_items["availability"].append(item["SK"])
            logger.info(f"Deleted {len(deleted_items['availability'])} availability slots")
        except Exception as e:
            logger.error(f"Error deleting availability slots: {e}")

        # 5. Delete bookings for this doctor
        try:
            # Bookings PK format: doctor_id#date (e.g., "DOC#uuid#2024-01-01")
            # We need to scan with filter expression for doctor_id
            # Use pagination to handle large tables
            paginator = config.booking_table.meta.client.get_paginator("scan")
            pages = paginator.paginate(
                TableName=config.booking_table.name,
                FilterExpression=Attr("doctor_id").eq(doctor_id)
            )
            
            for page in pages:
                for item in page.get("Items", []):
                    config.booking_table.delete_item(
                        Key={"PK": item["PK"], "SK": item["SK"]}
                    )
                    deleted_items["bookings"].append(item["PK"])
            logger.info(f"Deleted {len(deleted_items['bookings'])} bookings")
        except Exception as e:
            logger.error(f"Error deleting bookings: {e}")

        # 6. Delete S3 DoctorDocuments files
        try:
            prefix = f"DoctorDocuments/doctors/{doctor_id}/"
            paginator = config.s3_client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=config.S3_BUCKET, Prefix=prefix)
            
            for page in pages:
                if "Contents" in page:
                    for obj in page["Contents"]:
                        config.s3_client.delete_object(
                            Bucket=config.S3_BUCKET, Key=obj["Key"]
                        )
                        deleted_items["s3_files"].append(obj["Key"])
            logger.info(f"Deleted {len(deleted_items['s3_files'])} S3 files from DoctorDocuments")
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchKey":
                logger.error(f"Error deleting S3 files: {e}")
        except Exception as e:
            logger.error(f"Error deleting S3 files: {e}")

        # 7. Remove doctor from user subscribed_doctors lists
        try:
            # Scan users table to find subscribed_doctors lists containing this doctor_id
            # Use pagination to handle large tables
            paginator = config.users_table.meta.client.get_paginator("scan")
            pages = paginator.paginate(TableName=config.users_table.name)
            
            for page in pages:
                for user in page.get("Items", []):
                    subscribed_doctors = user.get("subscribed_doctors", [])
                    if doctor_id in subscribed_doctors:
                        subscribed_doctors.remove(doctor_id)
                        config.users_table.update_item(
                            Key={"user_id": user["user_id"]},
                            UpdateExpression="SET subscribed_doctors = :docs",
                            ExpressionAttributeValues={":docs": subscribed_doctors},
                        )
                        logger.info(f"Removed doctor {doctor_id} from user {user['user_id']} subscriptions")
        except Exception as e:
            logger.error(f"Error removing doctor from user subscriptions: {e}")

    except Exception as e:
        logger.error(f"Unexpected error in delete_doctor_account: {e}", exc_info=True)
        raise

    return deleted_items

