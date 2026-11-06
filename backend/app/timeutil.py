"""All instants are stored as naive UTC datetimes and converted at the edges."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def to_naive_utc(dt: datetime, assume: ZoneInfo | None = None) -> datetime:
    """Aware -> naive UTC. Naive (floating) values are read in `assume` (or UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=assume or UTC)
    return dt.astimezone(UTC).replace(tzinfo=None)


def iso_z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    """Parse an ISO string (Z or offset) into naive UTC; no offset means UTC."""
    return to_naive_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def get_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise ValueError(f"Unknown time zone: {name}")


def valid_zone(name: str) -> bool:
    try:
        get_zone(name)
        return True
    except ValueError:
        return False


def local_to_utc(d: date, minutes: int, tz: ZoneInfo) -> datetime | None:
    """Local wall-clock (date + minutes after midnight) -> naive UTC.

    Returns None for wall-clock times that don't exist (spring-forward gap).
    Ambiguous times (fall-back) resolve to the first occurrence.
    """
    base = datetime.combine(d, time(0, 0)) + timedelta(minutes=minutes)
    aware = base.replace(tzinfo=tz, fold=0)
    utc = aware.astimezone(UTC)
    if utc.astimezone(tz).replace(tzinfo=None) != base:
        return None
    return utc.replace(tzinfo=None)


def utc_to_local(dt: datetime, tz: ZoneInfo) -> datetime:
    return dt.replace(tzinfo=UTC).astimezone(tz)


def day_bounds_utc(d: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """UTC bounds of a local calendar day (23/25h on DST days)."""
    start = datetime.combine(d, time(0)).replace(tzinfo=tz).astimezone(UTC)
    end = datetime.combine(d + timedelta(days=1), time(0)).replace(tzinfo=tz).astimezone(UTC)
    return start.replace(tzinfo=None), end.replace(tzinfo=None)
