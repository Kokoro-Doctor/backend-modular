"""
Booking service with atomic operations and reusable DB functions.
Uses PK = doctor_id and SK = date#time format.
Now includes direct subscription validation (no Lambda invocations).
"""
from datetime import datetime, timedelta
from typing import Optional
from fastapi import HTTPException
from botocore.exceptions import ClientError
from app.config import AVAILABILITY_TABLE, APPOINTMENTS_TABLE
from app.utils.meet_utils import generate_jitsi_link
from app.utils.formatting_utils import format_booking_sk
from app.services.user_subscription_service import validate_subscription_for_booking, increment_appointments_used
from app.logger import get_logger
from boto3.dynamodb.conditions import Key
import uuid

logger = get_logger(__name__)


def get_booking_by_id(booking_id: str):
    """
    Get booking by booking_id using GSI_BookingId.
    Uses QUERY for efficient lookup.
    """
    try:
        response = APPOINTMENTS_TABLE.query(
            IndexName="GSI_BookingId",
            KeyConditionExpression=Key("booking_id").eq(booking_id)
        )
        items = response.get("Items", [])
        if not items:
            return None
        if len(items) > 1:
            logger.warning(f"Multiple bookings found with booking_id: {booking_id}")
        return items[0]
    except ClientError as e:
        logger.error(f"Error fetching booking by ID: {e}")
        raise HTTPException(500, "Failed to fetch booking")


def book_slot_atomically(doctor_id: str, date: str, start_time: str, user_id: str, validate_subscription: bool = True) -> dict:
    """
    Atomically book a slot using conditional updates.
    Uses 'available' boolean as the single source of truth.
    Validates subscription before booking and increments appointments after successful booking.
    Now uses direct function calls instead of Lambda invocations.
    Returns booking_id and booking details.
    """
    subscription_id = None
    
    # Validate subscription before booking (direct function call)
    if validate_subscription:
        try:
            validation = validate_subscription_for_booking(user_id, doctor_id)
            if not validation.get("is_valid", False):
                raise HTTPException(400, validation.get("message", "Subscription validation failed"))
            subscription_id = validation.get("subscription_id")
            logger.info(f"Subscription validated for user {user_id}, doctor {doctor_id}: {subscription_id}")
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Subscription validation failed, but continuing with booking: {str(e)}")
            # Optionally, you can make subscription validation mandatory by uncommenting:
            # raise HTTPException(400, "Subscription validation failed")
    
    pk = doctor_id  # PK = doctor_id directly
    sk = format_booking_sk(date, start_time)
    availability_sk = format_booking_sk(date, start_time)
    booking_id = str(uuid.uuid4())
    meet_link = generate_jitsi_link()
    
    try:
        # Atomically update availability to mark as booked
        # Condition: available must be True (single source of truth)
        try:
            AVAILABILITY_TABLE.update_item(
                Key={"PK": doctor_id, "SK": availability_sk},
                UpdateExpression="SET available = :available, user_id = :user_id, booking_id = :booking_id, meet_link = :meet_link",
                ConditionExpression="available = :true",
                ExpressionAttributeValues={
                    ":available": False,
                    ":user_id": user_id,
                    ":booking_id": booking_id,
                    ":meet_link": meet_link,
                    ":true": True
                }
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise HTTPException(400, "Slot already booked")
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                raise HTTPException(404, "Slot not found in availability")
            raise
        
        # Create booking record
        booking_item = {
            "PK": pk,
            "SK": sk,
            "doctor_id": doctor_id,
            "date": date,
            "start_time": start_time,
            "user_id": user_id,
            "booking_id": booking_id,
            "meet_link": meet_link,
            "created_at": datetime.utcnow().isoformat()
        }
        
        # Add subscription_id to booking if available
        if subscription_id:
            booking_item["subscription_id"] = subscription_id
        
        APPOINTMENTS_TABLE.put_item(Item=booking_item)
        
        # Increment subscription appointments after successful booking (direct function call)
        if subscription_id and validate_subscription:
            try:
                increment_appointments_used(subscription_id, user_id, doctor_id)
                logger.info(f"Incremented appointments for subscription {subscription_id}")
            except Exception as e:
                logger.error(f"Failed to increment subscription appointments: {str(e)}", exc_info=True)
                # Don't fail the booking if increment fails - booking is already created
                # This can be handled by a background job or manual correction
        
        logger.info(f"Successfully booked slot: {pk}#{sk} for user {user_id}")
        
        return {
            "booking_id": booking_id,
            "doctor_id": doctor_id,
            "date": date,
            "start_time": start_time,
            "user_id": user_id,
            "meet_link": meet_link,
            "created_at": booking_item["created_at"],
            "subscription_id": subscription_id
        }
        
    except HTTPException:
        raise
    except ClientError as e:
        logger.error(f"DynamoDB error booking slot: {e}")
        raise HTTPException(500, "Failed to book slot")
    except Exception as e:
        logger.error(f"Unexpected error booking slot: {e}")
        raise HTTPException(500, "Internal server error")


def cancel_booking_by_id(booking_id: str) -> dict:
    """
    Cancel a booking by booking_id.
    Marks slot as available and removes booking.
    Uses 'available' boolean as the single source of truth.
    """
    booking = get_booking_by_id(booking_id)
    if not booking:
        raise HTTPException(404, "Booking not found")
    
    pk = booking["PK"]
    sk = booking["SK"]
    doctor_id = booking["doctor_id"]
    date = booking["date"]
    start_time = booking["start_time"]
    availability_sk = format_booking_sk(date, start_time)
    
    try:
        # Delete booking
        APPOINTMENTS_TABLE.delete_item(Key={"PK": pk, "SK": sk})
        
        # Atomically mark slot as available again
        # Condition: available must be False (ensures slot was actually booked)
        try:
            AVAILABILITY_TABLE.update_item(
                Key={"PK": doctor_id, "SK": availability_sk},
                UpdateExpression="SET available = :available REMOVE user_id, booking_id, meet_link",
                ConditionExpression="available = :false",
                ExpressionAttributeValues={
                    ":available": True,
                    ":false": False
                }
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                logger.warning(f"Slot availability was not False when cancelling booking {booking_id}")
                # Continue anyway - booking is deleted
            else:
                raise
        
        logger.info(f"Successfully cancelled booking: {booking_id}")
        
        return {"message": "Booking cancelled successfully", "booking_id": booking_id}
        
    except ClientError as e:
        logger.error(f"DynamoDB error cancelling booking: {e}")
        raise HTTPException(500, "Failed to cancel booking")
    except Exception as e:
        logger.error(f"Unexpected error cancelling booking: {e}")
        raise HTTPException(500, "Internal server error")


def get_doctor_bookings_service(doctor_id: str, date: Optional[str] = None) -> list[dict]:
    """
    Get bookings for a doctor.
    If date provided, returns bookings for that date only.
    Returns sorted by time.
    """
    pk = doctor_id  # PK = doctor_id directly
    
    try:
        if date:
            # Query bookings for specific date
            sk_prefix = f"{date}#"
            response = APPOINTMENTS_TABLE.query(
                KeyConditionExpression=Key("PK").eq(pk) & Key("SK").begins_with(sk_prefix)
            )
        else:
            # Query all bookings for doctor
            response = APPOINTMENTS_TABLE.query(
                KeyConditionExpression=Key("PK").eq(pk)
            )
        
        bookings = response.get("Items", [])
        
        # Sort by date and time
        bookings.sort(key=lambda x: (x.get("date", ""), x.get("start_time", "")))
        
        return bookings
        
    except ClientError as e:
        logger.error(f"DynamoDB error fetching doctor bookings: {e}")
        raise HTTPException(500, "Failed to fetch bookings")


def get_user_bookings_service(user_id: str, booking_type: Optional[str] = None) -> list[dict]:
    """
    Get bookings for a user.
    If type='upcoming', returns only future bookings.
    If type='past', returns only past bookings.
    Returns sorted by date and time.
    """
    try:
        # Query using GSI
        response = APPOINTMENTS_TABLE.query(
            IndexName="GSI_UserBookings",
            KeyConditionExpression=Key("user_id").eq(user_id)
        )
        
        bookings = response.get("Items", [])
        today = datetime.utcnow().date()
        
        # Filter by type
        filtered_bookings = []
        for booking in bookings:
            booking_date_str = booking.get("date")
            if not booking_date_str:
                continue
            
            try:
                booking_date = datetime.strptime(booking_date_str, "%Y-%m-%d").date()
                booking_datetime = datetime.combine(
                    booking_date,
                    datetime.strptime(booking.get("start_time", "00:00"), "%H:%M").time()
                )
                
                if booking_type == "upcoming":
                    if booking_datetime > datetime.utcnow():
                        filtered_bookings.append(booking)
                elif booking_type == "past":
                    if booking_datetime < datetime.utcnow():
                        filtered_bookings.append(booking)
                else:
                    filtered_bookings.append(booking)
            except ValueError as e:
                logger.warning(f"Invalid date/time in booking: {e}")
                continue
        
        # Sort by date and time
        filtered_bookings.sort(key=lambda x: (
            x.get("date", ""),
            x.get("start_time", "")
        ))
        
        return filtered_bookings
        
    except ClientError as e:
        logger.error(f"DynamoDB error fetching user bookings: {e}")
        raise HTTPException(500, "Failed to fetch bookings")


def get_doctor_availability_service(doctor_id: str, date: str) -> list[dict]:
    """
    Get availability slots for a doctor on a specific date.
    Returns slot_time, available status, and booking_id if booked.
    """
    try:
        sk_prefix = f"{date}#"
        response = AVAILABILITY_TABLE.query(
            KeyConditionExpression=Key("PK").eq(doctor_id) & Key("SK").begins_with(sk_prefix)
        )
        
        slots = []
        for item in response.get("Items", []):
            sk_parts = item["SK"].split("#")
            if len(sk_parts) == 2:
                slot_time = sk_parts[1]
                slots.append({
                    "slot_time": slot_time,
                    "available": item.get("available", True),
                    "booking_id": item.get("booking_id"),
                    "user_id": item.get("user_id")
                })
        
        # Sort by time
        slots.sort(key=lambda x: x["slot_time"])
        
        return slots
        
    except ClientError as e:
        logger.error(f"DynamoDB error fetching availability: {e}")
        raise HTTPException(500, "Failed to fetch availability")


def get_doctor_calendar_service(doctor_id: str, days: int = 7) -> dict:
    """
    Get unified calendar for a doctor showing availability + bookings for next N days.
    Merges data from both AvailabilityTable and BookingsTable.
    """
    try:
        today = datetime.utcnow().date()
        calendar_slots = []
        
        # Get bookings for the date range
        pk = doctor_id  # PK = doctor_id directly
        bookings_response = APPOINTMENTS_TABLE.query(
            KeyConditionExpression=Key("PK").eq(pk)
        )
        bookings = bookings_response.get("Items", [])
        
        # Create a map of bookings by date#time
        bookings_map = {}
        for booking in bookings:
            date = booking.get("date")
            start_time = booking.get("start_time")
            if date and start_time:
                key = f"{date}#{start_time}"
                bookings_map[key] = booking
        
        # Get availability for each day
        for i in range(days):
            target_date = today + timedelta(days=i)
            date_str = target_date.strftime("%Y-%m-%d")
            sk_prefix = f"{date_str}#"
            
            # Query availability
            availability_response = AVAILABILITY_TABLE.query(
                KeyConditionExpression=Key("PK").eq(doctor_id) & Key("SK").begins_with(sk_prefix)
            )
            
            for item in availability_response.get("Items", []):
                sk_parts = item["SK"].split("#")
                if len(sk_parts) == 2:
                    slot_time = sk_parts[1]
                    key = f"{date_str}#{slot_time}"
                    booking = bookings_map.get(key)
                    
                    calendar_slots.append({
                        "date": date_str,
                        "slot_time": slot_time,
                        "available": item.get("available", True),
                        "booking_id": item.get("booking_id") or (booking.get("booking_id") if booking else None),
                        "user_id": item.get("user_id") or (booking.get("user_id") if booking else None)
                    })
        
        # Sort by date and time
        calendar_slots.sort(key=lambda x: (x["date"], x["slot_time"]))
        
        return {
            "doctor_id": doctor_id,
            "days": days,
            "slots": calendar_slots
        }
        
    except ClientError as e:
        logger.error(f"DynamoDB error fetching calendar: {e}")
        raise HTTPException(500, "Failed to fetch calendar")
    except Exception as e:
        logger.error(f"Unexpected error fetching calendar: {e}")
        raise HTTPException(500, "Internal server error")

