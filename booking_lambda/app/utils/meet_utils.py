"""
Utility functions for generating Jitsi Meet links.
"""
import uuid


def generate_jitsi_link() -> str:
    """
    Generate a unique Jitsi Meet link.
    
    Returns:
        A Jitsi Meet URL with a unique room name.
    """
    room_name = "kokoro-" + str(uuid.uuid4())
    link = f"https://meet.jit.si/{room_name}"
    return link

