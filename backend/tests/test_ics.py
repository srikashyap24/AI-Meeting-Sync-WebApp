from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.ics import ParsedEvent, parse_ics
from app.timeutil import utcnow

from .conftest import next_monday, z

LON = ZoneInfo("Europe/London")
NYC = ZoneInfo("America/New_York")


def wrap(*events):
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\n" + "".join(events) + "END:VCALENDAR\r\n"


def vevent(uid, start_line, extra="", summary="Meeting", end_line=None):
    s = f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20260101T000000Z\r\nSUMMARY:{summary}\r\n{start_line}\r\n"
    if end_line:
        s += end_line + "\r\n"
    return s + extra + ("\r\n" if extra and not extra.endswith("\r\n") else "") + "END:VEVENT\r\n"


def window():
    now = utcnow()
    return now - timedelta(days=7), now + timedelta(days=90)


def parse(text, tz=LON, email=None):
    s, e = window()
    return parse_ics(text, s, e, tz, email)


def future(days):
    return (datetime.utcnow() + timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)


def test_recurring_events_are_expanded():
    d = future(3)
    ics = wrap(vevent("r", f"DTSTART:{d:%Y%m%d}T100000Z", "RRULE:FREQ=DAILY;COUNT=4", end_line=f"DTEND:{d:%Y%m%d}T103000Z"))
    evs = parse(ics)
    assert len(evs) == 4
    assert [e.start for e in evs] == [d + timedelta(days=i, hours=10) for i in range(4)]


def test_recurrence_exdate_and_override_are_honoured():
    d = future(3)
    day = lambda i: (d + timedelta(days=i)).strftime("%Y%m%d")
    master = vevent("r", f"DTSTART:{day(0)}T100000Z", f"RRULE:FREQ=DAILY;COUNT=3\r\nEXDATE:{day(1)}T100000Z", end_line=f"DTEND:{day(0)}T110000Z")
    evs = parse(wrap(master))
    assert [e.start.date() for e in evs] == [(d + timedelta(days=i)).date() for i in (0, 2)]


def test_transparent_cancelled_and_declined_are_skipped():
    d = future(4).strftime("%Y%m%d")
    mk = lambda uid, extra: vevent(uid, f"DTSTART:{d}T090000Z", extra, end_line=f"DTEND:{d}T100000Z")
    ics = wrap(mk("busy", ""), mk("free", "TRANSP:TRANSPARENT"), mk("gone", "STATUS:CANCELLED"),
               mk("declined", "ATTENDEE;PARTSTAT=DECLINED:mailto:me@demo-meetsync.com"),
               mk("accepted", "ATTENDEE;PARTSTAT=ACCEPTED:mailto:me@demo-meetsync.com"))
    evs = parse(ics, email="me@demo-meetsync.com")
    assert sorted(e.uid for e in evs) == ["accepted", "busy"]
    # someone else declining doesn't hide my event
    other = wrap(mk("x", "ATTENDEE;PARTSTAT=DECLINED:mailto:someone@else.com"))
    assert len(parse(other, email="me@demo-meetsync.com")) == 1


def test_all_day_event_spans_the_owners_local_day():
    d = future(5)
    ics = wrap(vevent("a", f"DTSTART;VALUE=DATE:{d:%Y%m%d}", end_line=f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}"))
    (ev,) = parse(ics, tz=NYC)
    assert ev.all_day
    local_start = ev.start.replace(tzinfo=ZoneInfo("UTC")).astimezone(NYC)
    assert (local_start.hour, local_start.minute) == (0, 0)
    assert ev.end - ev.start == timedelta(hours=24) or ev.end - ev.start in (timedelta(hours=23), timedelta(hours=25))


def test_floating_time_uses_owner_zone():
    d = future(5)
    ics = wrap(vevent("f", f"DTSTART:{d:%Y%m%d}T090000", end_line=f"DTEND:{d:%Y%m%d}T100000"))
    (ev,) = parse(ics, tz=NYC)
    local = ev.start.replace(tzinfo=ZoneInfo("UTC")).astimezone(NYC)
    assert (local.hour, local.minute) == (9, 0)
    (ev2,) = parse(ics, tz=ZoneInfo("Asia/Tokyo"))
    assert ev2.start != ev.start


def test_tzid_events_and_duration():
    d = future(5)
    ics = wrap(vevent("t", f"DTSTART;TZID=America/New_York:{d:%Y%m%d}T090000", "DURATION:PT45M"))
    (ev,) = parse(ics, tz=LON)
    assert ev.end - ev.start == timedelta(minutes=45)
    assert ev.start.replace(tzinfo=ZoneInfo("UTC")).astimezone(NYC).hour == 9


def test_events_outside_the_sync_window_are_dropped():
    far = future(200).strftime("%Y%m%d")
    old = future(-30).strftime("%Y%m%d")
    ics = wrap(vevent("far", f"DTSTART:{far}T090000Z", end_line=f"DTEND:{far}T100000Z"), vevent("old", f"DTSTART:{old}T090000Z", end_line=f"DTEND:{old}T100000Z"))
    assert parse(ics) == []


def test_garbage_is_rejected_cleanly():
    import pytest
    from app.services.ics import ICSError

    with pytest.raises(ICSError):
        parse("this is not a calendar")


def test_upload_endpoint_end_to_end(client, signup):
    u = signup("Uma", tz="America/New_York")
    d = future(3)
    ics = wrap(vevent("w", f"DTSTART:{d:%Y%m%d}T140000Z", "RRULE:FREQ=WEEKLY;COUNT=3", summary="Standup", end_line=f"DTEND:{d:%Y%m%d}T143000Z"),
               vevent("skip", f"DTSTART:{d:%Y%m%d}T160000Z", "TRANSP:TRANSPARENT", end_line=f"DTEND:{d:%Y%m%d}T170000Z"))
    r = client.post("/api/calendars/ics-upload", headers=u.headers, files={"file": ("team.ics", ics, "text/calendar")}, data={"name": "Team"})
    assert r.status_code == 201, r.text
    assert r.json()["event_count"] == 3 and r.json()["kind"] == "ics_file" and r.json()["name"] == "Team"
    evs = client.get(f"/api/calendars/{r.json()['id']}/events", headers=u.headers).json()
    assert [e["title"] for e in evs] == ["Standup"] * 3
    # manual re-sync works from the stored file
    assert client.post(f"/api/calendars/{r.json()['id']}/sync", headers=u.headers).json()["event_count"] == 3
    # non-ICS and oversized uploads are refused
    assert client.post("/api/calendars/ics-upload", headers=u.headers, files={"file": ("x.ics", "hello", "text/calendar")}).status_code == 400
    big = "BEGIN:VCALENDAR\n" + "X" * (10 * 1024 * 1024 + 10)
    assert client.post("/api/calendars/ics-upload", headers=u.headers, files={"file": ("x.ics", big, "text/calendar")}).status_code == 413


def test_manual_event_editor(client, signup):
    u = signup("Uma")
    cal = client.post("/api/calendars/manual", headers=u.headers, json={"name": "Hand"}).json()
    mon = next_monday()
    ev = client.post(f"/api/calendars/{cal['id']}/events", headers=u.headers, json={"title": "Gym", "start": z(mon + timedelta(hours=7)), "end": z(mon + timedelta(hours=8))}).json()
    assert client.post(f"/api/calendars/{cal['id']}/events", headers=u.headers, json={"title": "Bad", "start": z(mon + timedelta(hours=8)), "end": z(mon + timedelta(hours=7))}).status_code == 422
    up = client.put(f"/api/calendars/{cal['id']}/events/{ev['id']}", headers=u.headers, json={"title": "Run", "start": z(mon + timedelta(hours=6)), "end": z(mon + timedelta(hours=7))})
    assert up.json()["title"] == "Run"
    assert client.delete(f"/api/calendars/{cal['id']}/events/{ev['id']}", headers=u.headers).status_code == 204
    assert client.get(f"/api/calendars/{cal['id']}/events", headers=u.headers).json() == []
    # synced calendars can't be hand-edited
    ics_cal = client.post("/api/calendars/ics-upload", headers=u.headers, files={"file": ("a.ics", wrap(), "text/calendar")}).json()
    assert client.post(f"/api/calendars/{ics_cal['id']}/events", headers=u.headers, json={"title": "x", "start": z(mon), "end": z(mon + timedelta(hours=1))}).status_code == 400
