"""Seed a demo team: 4 people in different time zones with realistic busy blocks.

    python seed_demo.py            # idempotent: re-running resets the demo data
Log in as leo@demo-meetsync.com / demo-pass-123 (the others use the same password).
The team's invite code is DEMO1234.
"""
import random
from datetime import date, timedelta

from app.db import SessionLocal, init_db
from app.models import CalendarSource, Event, Membership, Team, User
from app.security import hash_password
from app.services.sync import sync_calendar
from app.timeutil import get_zone, local_to_utc

PASSWORD = "demo-pass-123"
DOMAIN = "demo-meetsync.com"
INVITE = "DEMO1234"

# name, email-local, tz, (work start, work end) in local hours.
# Chosen so all four overlap only ~12:00-14:30 UTC (a realistic "narrow window" team).
PEOPLE = [
    ("Leo Hartmann", "leo", "Europe/London", (9, 17)),
    ("Anna Berg", "anna", "Europe/Berlin", (9, 17)),
    ("Ben Carter", "ben", "America/New_York", (7, 15)),
    ("Raj Patel", "raj", "Asia/Kolkata", (12, 20)),
]

# weekday (Mon=0) -> list of (start "HH:MM", end "HH:MM", title) in the person's own zone
RECURRING = {
    "leo": {"daily": [("10:00", "10:15", "Standup")], 1: [("14:00", "15:00", "Design review")], 3: [("11:00", "12:00", "1:1 with manager")]},
    "anna": {"daily": [("10:00", "10:15", "Standup")], 0: [("13:00", "14:30", "Sprint planning")], 2: [("15:00", "16:00", "Customer call")]},
    "ben": {"daily": [("09:00", "09:30", "Standup")], 1: [("10:00", "11:30", "Roadmap workshop")], 4: [("12:00", "13:00", "Lunch & learn")]},
    "raj": {"daily": [("15:30", "16:00", "Standup")], 2: [("17:00", "18:30", "Vendor sync")], 3: [("14:00", "15:00", "Interview panel")]},
}
PERSONAL = {
    "leo": [(0, "17:30", "18:30", "Gym"), (2, "12:30", "13:30", "Dentist")],
    "anna": [(1, "12:00", "13:00", "Lunch with Mara")],
    "ben": [(3, "16:00", "17:00", "School pickup")],
    "raj": [(4, "13:00", "14:00", "Doctor")],
}
# a few extra ad-hoc meetings landing in the shared 12:00-14:30 UTC window so the answer isn't trivial
ADHOC_UTC = [(1, "12:00", "13:00", "Incident review"), (2, "13:00", "14:00", "Partner call"), (3, "12:30", "13:30", "Hiring sync")]


def mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def add(db, cal, d: date, tz, start, end, title):
    s, e = local_to_utc(d, mins(start), tz), local_to_utc(d, mins(end), tz)
    if s and e and e > s:
        db.add(Event(calendar_id=cal.id, title=title, start_utc=s, end_utc=e))


def main():
    init_db()
    db = SessionLocal()
    old_users = db.query(User).filter(User.email.like(f"%@{DOMAIN}")).all()
    ids = [u.id for u in old_users]
    old_team_ids = [t.id for t in db.query(Team).filter(Team.invite_code == INVITE)]
    # SQLite doesn't enforce FKs by default, so clear dependents explicitly.
    db.query(Membership).filter(Membership.team_id.in_(old_team_ids) | Membership.user_id.in_(ids)).delete(synchronize_session=False)
    db.query(Team).filter(Team.id.in_(old_team_ids)).delete(synchronize_session=False)
    for u in old_users:
        db.delete(u)
    db.commit()

    rng = random.Random(7)
    today = date.today()
    days = [today + timedelta(days=i) for i in range(-3, 22)]
    users = {}
    for name, key, tz_name, (ws, we) in PEOPLE:
        u = User(email=f"{key}@{DOMAIN}", name=name, password_hash=hash_password(PASSWORD), timezone=tz_name,
                 work_start=ws * 60, work_end=we * 60, work_days="0,1,2,3,4")
        db.add(u)
        users[key] = u
    db.commit()

    team = Team(name="Product Squad", invite_code=INVITE, created_by=users["leo"].id)
    db.add(team)
    db.flush()
    for u in users.values():
        db.add(Membership(team_id=team.id, user_id=u.id))

    for _, key, tz_name, _ in PEOPLE:
        u, tz = users[key], get_zone(tz_name)
        work = CalendarSource(user_id=u.id, kind="manual", name="Work", share_titles=key in ("leo", "anna"))
        personal = CalendarSource(user_id=u.id, kind="manual", name="Personal", share_titles=False)
        db.add_all([work, personal])
        db.flush()
        for d in days:
            if d.weekday() >= 5:
                continue
            for s, e, title in RECURRING[key]["daily"] + RECURRING[key].get(d.weekday(), []):
                add(db, work, d, tz, s, e, title)
            for wd, s, e, title in PERSONAL[key]:
                if wd == d.weekday():
                    add(db, personal, d, tz, s, e, title)
            for wd, s, e, title in ADHOC_UTC:
                if wd == d.weekday() and rng.random() < 0.3:
                    local_s = local_to_utc(d, 0, get_zone("UTC"))  # anchor at 00:00 UTC of that date
                    st, en = local_s + timedelta(minutes=mins(s)), local_s + timedelta(minutes=mins(e))
                    db.add(Event(calendar_id=work.id, title=title, start_utc=st, end_utc=en))
    db.commit()

    # Ben also has a recurring sync imported from an .ics file (exercises the real ICS path)
    ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//MeetSync demo//EN\r\nBEGIN:VEVENT\r\nUID:ben-weekly\r\n"
           "DTSTAMP:20260101T000000Z\r\nDTSTART;TZID=America/New_York:20260105T133000\r\nDTEND;TZID=America/New_York:20260105T143000\r\n"
           "RRULE:FREQ=WEEKLY;BYDAY=MO,WE\r\nSUMMARY:Platform sync\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    cal = CalendarSource(user_id=users["ben"].id, kind="ics_file", name="Platform team (ICS)", ics_data=ics, share_titles=True)
    db.add(cal)
    db.commit()
    n = sync_calendar(db, cal, users["ben"])

    total = db.query(Event).count()
    print(f"Seeded {len(users)} users, team 'Product Squad' (invite {INVITE}), {total} events ({n} from ICS).")
    print(f"Log in with any of: {', '.join(u.email for u in users.values())}  /  password: {PASSWORD}")


if __name__ == "__main__":
    main()
