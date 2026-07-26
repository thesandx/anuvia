import secrets
import string
from datetime import datetime, timedelta, timezone

# India Standard Time is a fixed UTC+5:30 offset with no daylight saving,
# so a constant offset is correct and needs no timezone database.
IST = timezone(timedelta(hours=5, minutes=30))


def generate_token(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def format_ist(iso_utc: str) -> str | None:
    """Format an ISO-8601 UTC timestamp as a human string in IST.

    Returns None when the input is empty or cannot be parsed, so the health
    endpoint can show "never deployed with this build" instead of failing.
    """
    if not iso_utc:
        return None
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")
