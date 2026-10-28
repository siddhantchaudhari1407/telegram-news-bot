"""Shared utility helpers: IST timezone, date helpers."""
import zoneinfo
from datetime import datetime, date, timedelta, timezone

IST = zoneinfo.ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def get_ist_now() -> datetime:
    """Return current datetime in IST."""
    return datetime.now(tz=IST)


def get_yesterday_ist() -> date:
    """Return yesterday's date in IST."""
    return (get_ist_now() - timedelta(days=1)).date()


def to_ist(dt: datetime) -> datetime:
    """Convert a datetime to IST. If naive, assume UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(IST)


def is_yesterday_ist(dt: datetime) -> bool:
    """Return True if the datetime falls within yesterday in IST."""
    ist_dt = to_ist(dt)
    return ist_dt.date() == get_yesterday_ist()


def format_ist_time(dt: datetime) -> str:
    """Format datetime as HH:MM IST."""
    ist_dt = to_ist(dt)
    return ist_dt.strftime("%H:%M IST")
