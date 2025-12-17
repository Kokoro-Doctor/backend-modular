"""
Pure formatting utility functions.
These are stateless helper functions for formatting data.
"""


def format_booking_sk(date: str, start_time: str) -> str:
    """
    Format booking sort key: date#time
    
    Args:
        date: Date string in YYYY-MM-DD format
        start_time: Time string in HH:MM format
        
    Returns:
        Formatted sort key string (e.g., "2025-01-15#10:30")
    """
    return f"{date}#{start_time}"

