from google.oauth2 import service_account
from googleapiclient.discovery import build
import uuid

SERVICE_ACCOUNT_FILE = "credentials.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

def generate_meet_link():
    # Authenticate using service account
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )

    service = build("calendar", "v3", credentials=creds)

    # Create a minimal event just to generate Meet link
    request_id = str(uuid.uuid4())
    event = {
        "summary": "Kokoro Doctor Appointment",
        "start": {"dateTime": "2025-01-01T10:00:00+05:30"},
        "end": {"dateTime": "2025-01-01T10:30:00+05:30"},
        "conferenceData": {
            "createRequest": {
                "requestId": request_id,
                "conferenceSolutionKey": {"type": "hangoutsMeet"}
            }
        }
    }

    result = service.events().insert(
        calendarId="primary",
        body=event,
        conferenceDataVersion=1
    ).execute()

    meet_link = result["conferenceData"]["entryPoints"][0]["uri"]
    print("Google Meet Link:", meet_link)
    return meet_link

if __name__ == "__main__":
    generate_meet_link()
