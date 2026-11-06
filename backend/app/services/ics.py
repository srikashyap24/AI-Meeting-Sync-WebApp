"""ICS parsing: expands recurrences and returns plain busy intervals (naive UTC)."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events

from ..timeutil import to_naive_utc


@dataclass
class ParsedEvent:
    uid: str | None
    title: str
    start: datetime  # naive UTC
    end: datetime  # naive UTC
    all_day: bool
    location: str = ""
    description: str = ""


class ICSError(ValueError):
    pass


def _declined(component, user_email: str | None) -> bool:
    if not user_email:
        return False
    attendees = component.get("ATTENDEE")
    if attendees is None:
        return False
    if not isinstance(attendees, list):
        attendees = [attendees]
    me = user_email.lower()
    for a in attendees:
        if str(a).lower().replace("mailto:", "") == me and str(a.params.get("PARTSTAT", "")).upper() == "DECLINED":
            return True
    return False


def parse_ics(text: str, window_start: datetime, window_end: datetime, tz: ZoneInfo,
              user_email: str | None = None) -> list[ParsedEvent]:
    """window_* are naive UTC. Floating times and all-day dates are interpreted in `tz` (the owner's zone)."""
    try:
        cal = icalendar.Calendar.from_ical(text)
    except Exception as e:  # icalendar raises assorted ValueErrors
        raise ICSError("That doesn't look like a valid .ics calendar.") from e
    if not cal.walk("VEVENT"):
        return []

    # Pad the query window by a day each side so zone differences can't clip edge events.
    q_start = (window_start - timedelta(days=1)).replace(tzinfo=timezone.utc)
    q_end = (window_end + timedelta(days=1)).replace(tzinfo=timezone.utc)
    try:
        occurrences = recurring_ical_events.of(cal).between(q_start, q_end)
    except Exception as e:
        raise ICSError("Could not expand the recurring events in that calendar.") from e

    out: list[ParsedEvent] = []
    for ev in occurrences:
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        if str(ev.get("TRANSP", "OPAQUE")).upper() == "TRANSPARENT":
            continue
        if _declined(ev, user_email):
            continue
        start, end = ev.start, ev.end
        all_day = isinstance(start, date) and not isinstance(start, datetime)
        if all_day:
            s = datetime.combine(start, time(0)).replace(tzinfo=tz)
            e_date = end if isinstance(end, date) and not isinstance(end, datetime) else start + timedelta(days=1)
            e = datetime.combine(e_date, time(0)).replace(tzinfo=tz)
            s_utc, e_utc = to_naive_utc(s), to_naive_utc(e)
        else:
            s_utc = to_naive_utc(start, assume=tz)  # floating -> owner's zone
            e_utc = to_naive_utc(end, assume=tz) if isinstance(end, datetime) else s_utc
        if e_utc <= s_utc:
            continue
        if e_utc <= window_start or s_utc >= window_end:
            continue
        out.append(ParsedEvent(str(ev.get("UID", "")) or None, str(ev.get("SUMMARY", "") or ""), s_utc, e_utc, all_day,
                               str(ev.get("LOCATION", "") or "")[:500], str(ev.get("DESCRIPTION", "") or "")[:5000]))
    return out
