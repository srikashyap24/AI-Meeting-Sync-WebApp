from datetime import timedelta

from sqlalchemy import create_engine, inspect, text

from app.db import Base, ensure_columns
from app.services.ics import parse_ics
from app.timeutil import utcnow
from zoneinfo import ZoneInfo

from .conftest import next_monday, z


def make_team(client, signup):
    a, b = signup("Alice"), signup("Bob")
    t = client.post("/api/teams", headers=a.headers, json={"name": "T"}).json()
    client.post("/api/teams/join", headers=b.headers, json={"invite_code": t["invite_code"]})
    return a, b, t


def test_edit_event_details_and_owner_only_visibility(client, signup):
    a, b, t = make_team(client, signup)
    mon = next_monday()
    cal = client.post("/api/calendars/manual", headers=a.headers, json={"name": "Mine"}).json()["id"]
    client.patch(f"/api/calendars/{cal}", headers=a.headers, json={"share_titles": True})  # even with titles shared...
    ev = client.post(f"/api/calendars/{cal}/events", headers=a.headers, json={
        "title": "Review", "start": z(mon + timedelta(hours=10)), "end": z(mon + timedelta(hours=11)),
        "location": " Room 4 ", "description": "Bring the Q3 numbers"}).json()
    assert ev["location"] == "Room 4"
    up = client.put(f"/api/calendars/{cal}/events/{ev['id']}", headers=a.headers, json={
        "title": "Review v2", "start": z(mon + timedelta(hours=11)), "end": z(mon + timedelta(hours=12)), "location": "", "description": "Updated"})
    assert up.status_code == 200 and up.json()["title"] == "Review v2" and up.json()["location"] is None and up.json()["description"] == "Updated"

    url = f"/api/teams/{t['id']}/busy?start={z(mon)}&end={z(mon + timedelta(days=1))}"
    mine = next(m for m in client.get(url, headers=a.headers).json()["members"] if m["name"] == "Alice")["events"][0]
    assert mine["own"] and mine["editable"] and mine["calendar_name"] == "Mine" and mine["description"] == "Updated"
    seen = client.get(url, headers=b.headers)
    theirs = next(m for m in seen.json()["members"] if m["name"] == "Alice")["events"][0]
    assert theirs["title"] == "Review v2"  # titles were shared...
    assert "own" not in theirs and "description" not in theirs and "location" not in theirs and "calendar_id" not in theirs
    assert "Updated" not in seen.text  # ...but notes/location/calendar info never reach teammates


def test_synced_calendars_are_not_editable_and_report_it(client, signup):
    a, _, t = make_team(client, signup)
    mon = next_monday()
    ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:1\r\nDTSTAMP:20260101T000000Z\r\n"
           f"DTSTART:{mon + timedelta(hours=9):%Y%m%dT%H%M%S}Z\r\nDTEND:{mon + timedelta(hours=10):%Y%m%dT%H%M%S}Z\r\n"
           "SUMMARY:Board\r\nLOCATION:HQ\r\nDESCRIPTION:Agenda attached\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    c = client.post("/api/calendars/ics-upload", headers=a.headers, files={"file": ("a.ics", ics, "text/calendar")}).json()
    url = f"/api/teams/{t['id']}/busy?start={z(mon)}&end={z(mon + timedelta(days=1))}"
    e = next(m for m in client.get(url, headers=a.headers).json()["members"] if m["name"] == "Alice")["events"][0]
    assert e["editable"] is False and e["calendar_kind"] == "ics_file" and e["location"] == "HQ" and e["description"] == "Agenda attached"
    assert client.put(f"/api/calendars/{c['id']}/events/{e['id']}", headers=a.headers,
                      json={"title": "x", "start": z(mon), "end": z(mon + timedelta(hours=1))}).status_code == 400


def test_ics_location_and_description_are_parsed():
    d = (utcnow() + timedelta(days=3)).strftime("%Y%m%d")
    ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:1\r\nDTSTAMP:20260101T000000Z\r\n"
           f"DTSTART:{d}T090000Z\r\nDTEND:{d}T100000Z\r\nSUMMARY:X\r\nLOCATION:Lab\r\nDESCRIPTION:Notes here\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    (ev,) = parse_ics(ics, utcnow() - timedelta(days=7), utcnow() + timedelta(days=90), ZoneInfo("UTC"))
    assert (ev.location, ev.description) == ("Lab", "Notes here")


def test_old_database_gets_new_columns_without_losing_data():
    eng = create_engine("sqlite://")
    with eng.begin() as c:  # the events table exactly as it was before location/description existed
        c.execute(text("CREATE TABLE events (id INTEGER PRIMARY KEY, calendar_id INTEGER, uid VARCHAR(512), title VARCHAR(500), "
                       "start_utc DATETIME, end_utc DATETIME, all_day BOOLEAN)"))
        c.execute(text("INSERT INTO events (id, calendar_id, title, start_utc, end_utc, all_day) VALUES (1, 1, 'Old', '2026-01-01', '2026-01-01', 0)"))
    from app import models  # noqa: F401
    ensure_columns(eng)
    ensure_columns(eng)  # idempotent
    assert {"location", "description"} <= {c["name"] for c in inspect(eng).get_columns("events")}
    with eng.connect() as c:
        assert c.execute(text("SELECT title, location FROM events")).one() == ("Old", None)
