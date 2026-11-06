"""Glue between the DB, the NL layer and the deterministic availability engine."""
import logging
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from ..models import CalendarSource, Event, Membership, Team, User
from ..timeutil import get_zone, iso_z, local_to_utc, utc_to_local
from . import nlp
from .availability import Candidate, Constraints, Person, diagnose, find_slots

log = logging.getLogger("meetsync.schedule")


def team_users(db: Session, team: Team) -> list[User]:
    return [m.user for m in db.query(Membership).filter_by(team_id=team.id).all()]


def roster_of(users: list[User]) -> list[nlp.RosterMember]:
    return [nlp.RosterMember(u.id, u.name, u.timezone) for u in users]


def load_people(db: Session, users: list[User], win_start: datetime, win_end: datetime) -> dict[int, Person]:
    ids = [u.id for u in users]
    rows = (db.query(CalendarSource.user_id, Event.start_utc, Event.end_utc)
            .join(Event, Event.calendar_id == CalendarSource.id)
            .filter(CalendarSource.user_id.in_(ids), Event.end_utc > win_start, Event.start_utc < win_end).all())
    busy: dict[int, list] = {i: [] for i in ids}
    for uid, s, e in rows:
        busy[uid].append((s, e))
    return {u.id: Person(u.id, u.name, get_zone(u.timezone), u.work_start, u.work_end, frozenset(u.work_days_list), busy[u.id])
            for u in users}


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def to_constraints(p: nlp.Parsed, max_results: int = 5) -> Constraints:
    return Constraints(p.required_ids, p.optional_ids, p.duration_minutes, p.start_date, p.end_date, _hm(p.earliest),
                       _hm(p.latest), frozenset(p.weekdays) if p.weekdays else None, p.preference, p.buffer_minutes,
                       max_results=max_results)


def _slot_json(c: Candidate, people: dict[int, Person], required: list[int], optional: list[int], req_tz) -> dict:
    parts = []
    for pid in required + optional:
        p = people[pid]
        ls, le = utc_to_local(c.start, p.tz), utc_to_local(c.end, p.tz)
        parts.append({"id": pid, "name": p.name, "timezone": str(p.tz), "role": "required" if pid in required else "optional",
                      "attending": pid in required or pid in c.available_optional,
                      "local_start": ls.strftime("%Y-%m-%dT%H:%M"), "local_end": le.strftime("%Y-%m-%dT%H:%M")})
    return {"start": iso_z(c.start), "end": iso_z(c.end), "score": c.score,
            "date": utc_to_local(c.start, req_tz).date().isoformat(),
            "available_optional_ids": c.available_optional, "missing_optional_ids": c.missing_optional,
            "participants": parts}


def _summary(slots: list[dict], req_name: str) -> str:
    out = []
    for i, s in enumerate(slots, 1):
        who = "; ".join(f"{p['name']} {p['local_start'][11:]}-{p['local_end'][11:]}" + ("" if p["attending"] else " (cannot attend)")
                        for p in s["participants"])
        out.append(f"{i}. {s['participants'][0]['local_start'][:10]} (requester zone) | local times: {who} | score {s['score']}")
    return "\n".join(out)


def _rule_recommendation(slots: list[dict], requester: User) -> str:
    if not slots:
        return ""
    best = slots[0]
    req_tz = get_zone(requester.timezone)
    ls = utc_to_local(datetime.strptime(best["start"], "%Y-%m-%dT%H:%M:%SZ"), req_tz)
    le = utc_to_local(datetime.strptime(best["end"], "%Y-%m-%dT%H:%M:%SZ"), req_tz)
    others = [f"{p['name']} at {p['local_start'][11:]}" for p in best["participants"] if p["id"] != requester.id and p["attending"]]
    text = f"The top pick is {ls.strftime('%A %d %b')}, {ls.strftime('%H:%M')}-{le.strftime('%H:%M')} your time"
    text += f" ({', '.join(others)} in their local time)." if others else "."
    if best["missing_optional_ids"]:
        text += " Not everyone optional can make it."
    if len(slots) > 1:
        text += f" {len(slots) - 1} other option{'s' if len(slots) > 2 else ''} {'are' if len(slots) > 2 else 'is'} listed below if that doesn't suit."
    return text


def run_search(db: Session, team: Team, requester: User, parsed: nlp.Parsed, now: datetime, text: str = "",
               parser: str = "structured", client=None, max_results: int = 5) -> dict:
    users = team_users(db, team)
    by_id = {u.id: u for u in users}
    req_tz = get_zone(requester.timezone)
    c = to_constraints(parsed, max_results)
    lo = local_to_utc(c.start_date - timedelta(days=1), 0, req_tz) or datetime.combine(c.start_date, datetime.min.time())
    hi = datetime.combine(c.end_date + timedelta(days=3), datetime.min.time())
    wanted = [by_id[i] for i in dict.fromkeys(c.required_ids + c.optional_ids)]
    people = load_people(db, wanted, lo - timedelta(days=1), hi)

    cands = find_slots(people, c, req_tz, now)
    slots = [_slot_json(x, people, c.required_ids, c.optional_ids, req_tz) for x in cands]
    diagnostics = None if slots else diagnose(people, c, req_tz, now)

    recommendation = diagnostics["explanation"] if diagnostics else _rule_recommendation(slots, requester)
    rec_source = "rules"
    if slots and client is not None:
        try:
            recommendation = nlp.claude_recommend(text or "Find a meeting time", _summary(slots, requester.name), client)
            rec_source = "claude"
        except Exception as e:  # any API failure must degrade, never break the search
            log.warning("Claude recommendation failed: %s", e)

    name = lambda i: by_id[i].name
    return {
        "parser": parser,
        "parsed": {
            "required": [{"id": i, "name": name(i)} for i in parsed.required_ids],
            "optional": [{"id": i, "name": name(i)} for i in parsed.optional_ids],
            "duration_minutes": parsed.duration_minutes, "start_date": parsed.start_date.isoformat(),
            "end_date": parsed.end_date.isoformat(), "earliest": parsed.earliest, "latest": parsed.latest,
            "weekdays": parsed.weekdays, "preference": parsed.preference, "buffer_minutes": parsed.buffer_minutes,
            "timezone": requester.timezone,
        },
        "unknown_names": parsed.unknown_names,
        "notes": parsed.notes,
        "slots": slots,
        "recommendation": recommendation,
        "recommendation_source": rec_source,
        "diagnostics": diagnostics,
    }


def schedule_from_text(db: Session, team: Team, requester: User, text: str, now: datetime) -> dict:
    users = team_users(db, team)
    roster = roster_of(users)
    me = next(m for m in roster if m.id == requester.id)
    today = utc_to_local(now, get_zone(requester.timezone)).date()
    client = nlp.get_client()
    raw, parser = None, "rules"
    if client is not None:
        try:
            raw, parser = nlp.claude_parse(text, roster, me, today, client), "claude"
        except Exception as e:
            log.warning("Claude parse failed, using rule parser: %s", e)
    if raw is None:
        raw = nlp.rule_parse(text, roster, requester.id, today)
    parsed = nlp.sanitize(raw, roster, requester.id, today)
    return run_search(db, team, requester, parsed, now, text, parser, client)


def schedule_from_structured(db: Session, team: Team, requester: User, body, now: datetime) -> dict:
    """POST /suggest: the same constraints as JSON. Ids must be members of this team."""
    roster = roster_of(team_users(db, team))
    valid = {m.id for m in roster}
    bad = [i for i in body.required_ids + body.optional_ids if i not in valid]
    if bad:
        raise ValueError(f"Not members of this team: {sorted(set(bad))}")
    today = utc_to_local(now, get_zone(requester.timezone)).date()
    raw = {"required_attendee_ids": body.required_ids, "optional_attendee_ids": body.optional_ids,
           "duration_minutes": body.duration_minutes,
           "start_date": body.start_date.isoformat() if body.start_date else None,
           "end_date": body.end_date.isoformat() if body.end_date else None,
           "earliest_time": body.earliest, "latest_time": body.latest, "weekdays": body.weekdays,
           "preference": body.preference, "buffer_minutes": body.buffer_minutes}
    parsed = nlp.sanitize(raw, roster, requester.id, today)
    return run_search(db, team, requester, parsed, now, parser="structured", client=nlp.get_client())
