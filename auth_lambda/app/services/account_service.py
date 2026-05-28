"""
Account service - handles account deletion business logic.
"""
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from app import config
from app.logger import get_logger
from app.utils.db_utils import normalize_phone_number
from app.services.user_service import user_exists_by_phone
from app.services.doctor_service import doctor_exists_by_phone

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
            response = config.appointments_table.query(
                IndexName="GSI_UserBookings",
                KeyConditionExpression=Key("user_id").eq(user_id)
            )
            for item in response.get("Items", []):
                config.appointments_table.delete_item(
                    Key={"PK": item["PK"], "SK": item["SK"]}
                )
                deleted_items["bookings"].append(item.get("SK", "unknown"))
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

        # 7. Cancel user subscriptions in UserDoctorSubscriptions table
        # Note: Subscription data is stored in UserDoctorSubscriptions table, not in User/Doctor tables
        # Subscriptions should be cancelled via the subscription service, not by modifying User/Doctor tables
        try:
            # Import subscription service if available (may require Lambda invocation)
            logger.info(f"User {user_id} subscriptions should be cancelled via subscription service")
            logger.info("Subscriptions are stored in UserDoctorSubscriptions table, not in User/Doctor tables")
            # TODO: Cancel subscriptions via subscription service endpoint if needed
        except Exception as e:
            logger.error(f"Error handling user subscriptions: {e}")

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
            pk = doctor_id
            response = config.appointments_table.query(
                KeyConditionExpression=Key("PK").eq(pk)
            )
            for item in response.get("Items", []):
                config.appointments_table.delete_item(
                    Key={"PK": item["PK"], "SK": item["SK"]}
                )
                deleted_items["bookings"].append(item.get("SK", "unknown"))
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

        # 7. Cancel doctor subscriptions in UserDoctorSubscriptions table
        # Note: Subscription data is stored in UserDoctorSubscriptions table, not in User/Doctor tables
        # Subscriptions should be cancelled via the subscription service, not by modifying User/Doctor tables
        try:
            # Import subscription service if available (may require Lambda invocation)
            logger.info(f"Doctor {doctor_id} subscriptions should be cancelled via subscription service")
            logger.info("Subscriptions are stored in UserDoctorSubscriptions table, not in User/Doctor tables")
            # TODO: Cancel subscriptions via subscription service endpoint if needed
        except Exception as e:
            logger.error(f"Error handling doctor subscriptions: {e}")

    except Exception as e:
        logger.error(f"Unexpected error in delete_doctor_account: {e}", exc_info=True)
        raise

    return deleted_items

