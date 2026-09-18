"""Formats a UTC-aware datetime as a speech-friendly local-time string.

Shared by the live prompt (the patient's own appointment) and the
reschedule tool (candidate slots), so both read a time aloud the same
way, and neither ever hands Nova Sonic a raw timestamp to convert
itself -- exactly the class of bug this project's timezone fix exists to
close off. See `app.agents.bidi.prompts` and `app.services.scheduling_service`.
"""

from datetime import datetime
from zoneinfo import ZoneInfo


def _clock_phrase(local: datetime) -> str:
    hour = int(local.strftime("%I"))
    period = (
        "in the morning"
        if local.hour < 12
        else "in the afternoon"
        if local.hour < 17
        else "in the evening"
    )
    clock = f"{hour}:{local.minute:02d}" if local.minute else str(hour)
    return f"{clock} {period}"


def format_spoken_time(at: datetime, tz_name: str) -> str:
    """E.g. "Tuesday at 10 in the morning" -- day-of-week only, for a
    near-term reschedule offer where the exact date is unambiguous."""
    local = at.astimezone(ZoneInfo(tz_name))
    return f"{local.strftime('%A')} at {_clock_phrase(local)}"


def format_spoken_datetime(at: datetime, tz_name: str) -> str:
    """E.g. "Tuesday 23 September at 10 in the morning" -- includes the
    actual date, for announcing a booked appointment that could be weeks
    out, where day-of-week alone would be ambiguous."""
    local = at.astimezone(ZoneInfo(tz_name))
    return f"{local.strftime('%A %d %B')} at {_clock_phrase(local)}"
