from datetime import timedelta

import httpx
from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..models import CalendarSource, Event, User
from ..security import UnsafeURL, fetch_ics_text
from ..timeutil import get_zone, utcnow
from . import providers
from .ics import ICSError, ParsedEvent, parse_ics

PAST_DAYS = 7
FUTURE_DAYS = 90


class SyncError(Exception):
    pass


def sync_window():
    now = utcnow()
    return now - timedelta(days=PAST_DAYS), now + timedelta(days=FUTURE_DAYS)


def sync_calendar(db: Session, cal: CalendarSource, user: User, http: httpx.Client | None = None) -> int:
    """Replace the calendar's cached events with a fresh pull. Returns the number of events stored."""
    if cal.kind == "manual":
        return db.query(Event).filter(Event.calendar_id == cal.id).count()

    start, end = sync_window()
    tz = get_zone(user.timezone)
    own = http is None
    http = http or providers.make_client()
    try:
        if cal.kind == "ics_url":
            # make_client() never follows redirects itself; fetch_ics_text validates every hop
            parsed = parse_ics(fetch_ics_text(cal.ics_url or "", http), start, end, tz, user.email)
        elif cal.kind == "ics_file":
            parsed = parse_ics(cal.ics_data or "", start, end, tz, user.email)
        elif cal.kind in ("google", "microsoft"):
            token = providers.valid_access_token(cal, http)
            fetch = providers.fetch_google if cal.kind == "google" else providers.fetch_microsoft
            parsed = fetch(token, start, end, tz, http)
        else:
            raise SyncError(f"Unknown calendar kind: {cal.kind}")
    except (ICSError, UnsafeURL, providers.ProviderError, ValueError) as e:
        cal.last_error = str(e)[:500]
        db.commit()
        raise SyncError(cal.last_error) from e
    except httpx.HTTPError as e:
        cal.last_error = f"Network error talking to the calendar provider ({e.__class__.__name__})."
        db.commit()
        raise SyncError(cal.last_error) from e
    finally:
        if own:
            http.close()

    db.execute(delete(Event).where(Event.calendar_id == cal.id))
    db.add_all(Event(calendar_id=cal.id, uid=p.uid, title=p.title[:500], start_utc=p.start, end_utc=p.end, all_day=p.all_day,
                     location=p.location or None, description=p.description or None)
               for p in parsed)
    cal.last_synced_at = utcnow()
    cal.last_error = None
    db.commit()
    return len(parsed)
