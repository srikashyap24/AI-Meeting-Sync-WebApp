"""Deterministic availability engine. Pure functions: no DB, no network, no LLM.

Candidates are 15-minute-aligned starts in the requester's zone. A person is "available" for a
candidate when the meeting lies inside their own working hours (their zone, their working weekdays)
and no busy block touches [start - buffer, end + buffer).
"""
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..timeutil import local_to_utc, utc_to_local

STEP = 15
LUNCH = (12 * 60, 13 * 60)
COMFORT_START = 9 * 60
COMFORT_END = 18 * 60


@dataclass
class Person:
    id: int
    name: str
    tz: ZoneInfo
    work_start: int  # minutes after local midnight
    work_end: int
    work_days: frozenset[int]  # Mon=0
    busy: list[tuple[datetime, datetime]] = field(default_factory=list)  # naive UTC

    def __post_init__(self):
        self._starts, self._ends = _merge(self.busy)

    def is_free(self, start: datetime, end: datetime) -> bool:
        """True if no merged busy interval overlaps [start, end)."""
        i = bisect_right(self._ends, start)  # first merged interval that ends after `start`
        return not (i < len(self._starts) and self._starts[i] < end)

    def in_working_hours(self, start: datetime, end: datetime) -> bool:
        ls = utc_to_local(start, self.tz)
        if ls.weekday() not in self.work_days:
            return False
        sm = ls.hour * 60 + ls.minute
        em = sm + int((end - start).total_seconds() // 60)  # elapsed minutes; work_end <= 24:00 so this can't wrap midnight
        return self.work_start <= sm and em <= self.work_end


def _merge(intervals):
    ivs = sorted((s, e) for s, e in intervals if e > s)
    merged: list[list[datetime]] = []
    for s, e in ivs:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [m[0] for m in merged], [m[1] for m in merged]


@dataclass
class Constraints:
    required_ids: list[int]
    optional_ids: list[int]
    duration_minutes: int
    start_date: date  # requester-zone dates, inclusive
    end_date: date
    earliest_minute: int = 0  # requester zone; meeting must start >= earliest and end <= latest
    latest_minute: int = 24 * 60
    weekdays: frozenset[int] | None = None  # requester-zone weekdays; None = any
    preference: str | None = None  # "morning" | "afternoon" | None
    buffer_minutes: int = 0
    max_results: int = 5
    per_day_cap: int = 2


@dataclass
class Candidate:
    start: datetime
    end: datetime
    score: float
    available_optional: list[int]
    missing_optional: list[int]


def _comfort_penalty(p: Person, start: datetime, end: datetime) -> float:
    ls = utc_to_local(start, p.tz)
    sm = ls.hour * 60 + ls.minute
    em = sm + int((end - start).total_seconds() // 60)
    pen = 0.0
    if sm < COMFORT_START:
        pen += (COMFORT_START - sm) / 6  # 1h early = -10
    if em > COMFORT_END:
        pen += (em - COMFORT_END) / 6
    overlap = max(0, min(em, LUNCH[1]) - max(sm, LUNCH[0]))
    pen += overlap / 3  # a whole lunch hour = -20
    return pen


def iter_candidates(c: Constraints, requester_tz: ZoneInfo, now: datetime):
    """Yield (start_utc, end_utc, local_start_minute) honouring date/weekday/time-of-day filters."""
    day = c.start_date
    dur = timedelta(minutes=c.duration_minutes)
    while day <= c.end_date:
        if c.weekdays is None or day.weekday() in c.weekdays:
            m = c.earliest_minute - c.earliest_minute % STEP
            if m < c.earliest_minute:
                m += STEP
            while m + c.duration_minutes <= c.latest_minute:
                s = local_to_utc(day, m, requester_tz)
                if s is not None and s >= now:
                    yield s, s + dur, m
                m += STEP
        day += timedelta(days=1)


def find_slots(people: dict[int, Person], c: Constraints, requester_tz: ZoneInfo, now: datetime) -> list[Candidate]:
    req = [people[i] for i in c.required_ids]
    opt = [people[i] for i in c.optional_ids if i in people and i not in c.required_ids]
    buf = timedelta(minutes=c.buffer_minutes)
    scored: list[Candidate] = []
    for s, e, local_min in iter_candidates(c, requester_tz, now):
        if not all(p.in_working_hours(s, e) and p.is_free(s - buf, e + buf) for p in req):
            continue
        avail = [p for p in opt if p.in_working_hours(s, e) and p.is_free(s - buf, e + buf)]
        score = 100.0
        if opt:
            score += 15 * len(avail) / len(opt)
        if c.preference == "morning" and local_min < 12 * 60:
            score += 20
        elif c.preference == "afternoon" and local_min >= 12 * 60:
            score += 20
        days_out = (utc_to_local(s, requester_tz).date() - c.start_date).days
        score -= min(days_out, 30) * 0.8
        score -= sum(_comfort_penalty(p, s, e) for p in req + avail)
        scored.append(Candidate(s, e, round(score, 2), [p.id for p in avail], [p.id for p in opt if p not in avail]))
    return pick_varied(scored, c, requester_tz)


def pick_varied(scored: list[Candidate], c: Constraints, requester_tz: ZoneInfo) -> list[Candidate]:
    """Greedy best-first: no overlapping options, at most `per_day_cap` per requester-zone day.
    Options on a day that already has a pick are demoted slightly so results spread across days."""
    chosen: list[Candidate] = []
    per_day: dict[date, int] = {}
    pool = sorted(scored, key=lambda x: (-x.score, x.start))
    while pool and len(chosen) < c.max_results:
        def adj(x: Candidate) -> float:
            return x.score - 8 * per_day.get(utc_to_local(x.start, requester_tz).date(), 0)
        best = max(pool, key=lambda x: (adj(x), -x.start.timestamp()))
        pool.remove(best)
        d = utc_to_local(best.start, requester_tz).date()
        if per_day.get(d, 0) >= c.per_day_cap:
            continue
        if any(best.start < o.end + timedelta(minutes=c.buffer_minutes) and o.start < best.end + timedelta(minutes=c.buffer_minutes)
               for o in chosen):
            continue
        chosen.append(best)
        per_day[d] = per_day.get(d, 0) + 1
    return sorted(chosen, key=lambda x: x.start)


# ---------------- diagnostics ----------------

def _fmt_hours(p: Person) -> str:
    f = lambda m: f"{m // 60:02d}:{m % 60:02d}"
    return f"{f(p.work_start)}-{f(p.work_end)}"


def diagnose(people: dict[int, Person], c: Constraints, requester_tz: ZoneInfo, now: datetime) -> dict:
    """Explain an empty result. Counts per person, and whether working hours overlap at all."""
    req = [people[i] for i in c.required_ids]
    buf = timedelta(minutes=c.buffer_minutes)
    cands = list(iter_candidates(c, requester_tz, now))
    per_person = []
    all_work = 0
    all_free = 0
    for p in req:
        w = f = 0
        for s, e, _ in cands:
            iw = p.in_working_hours(s, e)
            w += iw
            f += iw and p.is_free(s - buf, e + buf)
        per_person.append({"id": p.id, "name": p.name, "timezone": str(p.tz), "working_hours": _fmt_hours(p),
                           "working_hours_candidates": w, "free_candidates": f})
    for s, e, _ in cands:
        if all(p.in_working_hours(s, e) for p in req):
            all_work += 1
            if all(p.is_free(s - buf, e + buf) for p in req):
                all_free += 1

    # Would the working-hours overlap exist if the requester's time-of-day / weekday filters were removed?
    wide = Constraints(**{**c.__dict__, "earliest_minute": 0, "latest_minute": 24 * 60, "weekdays": None})
    overlap_without_filters = any(
        all(p.in_working_hours(s, e) for p in req) for s, e, _ in iter_candidates(wide, requester_tz, now)
    )
    overlap = all_work > 0
    return {
        "total_candidates": len(cands),
        "per_person": per_person,
        "working_hours_overlap": overlap,
        "working_hours_overlap_without_filters": overlap_without_filters,
        "all_working_hours_candidates": all_work,
        "explanation": _explain(req, c, cands, per_person, overlap, overlap_without_filters, all_work),
    }


def _explain(req, c, cands, per_person, overlap, overlap_wide, all_work) -> str:
    if not cands:
        return ("No time is left to check: the date range, weekday and time-of-day limits leave no "
                f"{c.duration_minutes}-minute windows in the future. Widen the dates or the allowed hours.")
    names = ", ".join(f"{p.name} ({p.tz}, {_fmt_hours(p)})" for p in req)
    if len(req) > 1 and not overlap:
        if overlap_wide:
            return ("Everyone's working hours do overlap in principle, but not inside the time-of-day or weekday limits "
                    f"you set. Try loosening the earliest/latest times or allowing more days. Working hours: {names}.")
        return ("Working hours don't overlap across these time zones for a "
                f"{c.duration_minutes}-minute meeting, so there is no time when everyone is inside their own working day. "
                f"Working hours: {names}. Someone would need to meet outside their normal hours, or you could shorten the meeting.")
    zero = [d for d in per_person if d["working_hours_candidates"] and d["free_candidates"] == 0]
    if len(zero) == 1 or (zero and len(zero) < len(per_person)):
        who = ", ".join(d["name"] for d in zero)
        return (f"Working hours overlap, but {who} {'is' if len(zero) == 1 else 'are'} fully booked "
                "for every possible time in this range. Try a later date range or drop them as optional.")
    return ("Working hours overlap, but everyone's calendars together leave no common gap of "
            f"{c.duration_minutes} minutes{' (plus buffers)' if c.buffer_minutes else ''} in this range. "
            "Try a shorter meeting, a smaller buffer, or a wider date range.")
