from datetime import datetime, timezone, timedelta


def date_to_timestamp_range(date_str: str) -> tuple[int, int]:
    """Convert a YYYY-MM-DD string to (start_ts, end_ts) covering the full UTC day."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start_ts = int(dt.timestamp())
    end_ts = int((dt + timedelta(days=1)).timestamp()) - 1
    return start_ts, end_ts
