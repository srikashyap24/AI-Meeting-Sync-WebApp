from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.availability import Constraints, Person, diagnose, find_slots
from app.timeutil import local_to_utc, utc_to_local

NOW = datetime(2026, 11, 2, 0, 0)  # Monday, naive UTC
MON = date(2026, 11, 9)
WORKDAYS = frozenset(range(5))
UTC, LON, NYC, TOKYO = (ZoneInfo(z) for z in ("UTC", "Europe/London", "America/New_York", "Asia/Tokyo"))


def person(i, tz=UTC, start=9, end=17, busy=()):
    return Person(i, f"P{i}", tz, start * 60, end * 60, WORKDAYS, list(busy))


def cons(req, opt=(), **kw):
    base = dict(required_ids=list(req), optional_ids=list(opt), duration_minutes=30, start_date=MON, end_date=MON + timedelta(days=4),
                max_results=50, per_day_cap=50)
    base.update(kw)
    return Constraints(**base)


def run(people, c, tz=UTC):
    return find_slots({p.id: p for p in people}, c, tz, NOW)


def at(day, h, m=0):
    return datetime.combine(day, datetime.min.time()) + timedelta(hours=h, minutes=m)


def test_avoids_busy_blocks():
    a = person(1, busy=[(at(MON, 9), at(MON, 12))])
    b = person(2)
    slots = run([a, b], cons([1, 2], end_date=MON, duration_minutes=60))
    assert slots
    assert all(s.start >= at(MON, 12) for s in slots)  # morning is fully blocked
    assert all(not (s.start < at(MON, 12) and s.end > at(MON, 9)) for s in slots)


def test_respects_each_persons_working_hours_across_time_zones():
    london, nyc = person(1, LON), person(2, NYC)  # London 09-17 UTC, New York 14-22 UTC in November
    slots = run([london, nyc], cons([1, 2]))
    assert slots
    for s in slots:
        assert at(s.start.date(), 14) <= s.start and s.end <= at(s.start.date(), 17)


def test_working_hours_with_no_overlap_returns_diagnostic():
    london, tokyo = person(1, LON), person(2, TOKYO)  # Tokyo 09-17 = 00-08 UTC
    c = cons([1, 2])
    people = {1: london, 2: tokyo}
    assert find_slots(people, c, UTC, NOW) == []
    d = diagnose(people, c, UTC, NOW)
    assert d["working_hours_overlap"] is False
    assert d["all_working_hours_candidates"] == 0
    assert "don't overlap" in d["explanation"]
    assert {p["name"] for p in d["per_person"]} == {"P1", "P2"}


def test_diagnostic_blames_filters_when_hours_do_overlap_in_principle():
    london, nyc = person(1, LON), person(2, NYC)
    c = cons([1, 2], earliest_minute=9 * 60, latest_minute=12 * 60)  # NY is asleep/outside hours until 14 UTC
    d = diagnose({1: london, 2: nyc}, c, UTC, NOW)
    assert d["working_hours_overlap"] is False and d["working_hours_overlap_without_filters"] is True
    assert "time-of-day" in d["explanation"]


def test_diagnostic_counts_free_candidates_when_someone_is_fully_booked():
    busy = [(at(MON + timedelta(days=i), 0), at(MON + timedelta(days=i), 23)) for i in range(5)]
    a, b = person(1), person(2, busy=busy)
    d = diagnose({1: a, 2: b}, cons([1, 2]), UTC, NOW)
    by = {p["id"]: p for p in d["per_person"]}
    assert by[1]["free_candidates"] > 0 and by[2]["free_candidates"] == 0
    assert d["working_hours_overlap"] is True
    assert "P2" in d["explanation"] and "fully booked" in d["explanation"]


def test_optional_attendee_raises_score_and_is_reported():
    opt_busy = [(at(MON, 9), at(MON, 13))]
    req, opt = person(1), person(2, busy=opt_busy)
    slots = run([req, opt], cons([1], [2], end_date=MON))
    best = max(slots, key=lambda s: s.score)
    assert 2 in best.available_optional and best.start >= at(MON, 13)
    worse = [s for s in slots if 2 in s.missing_optional]
    assert worse and all(s.score < best.score for s in worse)
    # an optional attendee never blocks a slot
    assert any(2 in s.missing_optional for s in slots)


def test_buffers_push_slots_away_from_busy_blocks():
    a = person(1, busy=[(at(MON, 10), at(MON, 11))])
    kw = dict(end_date=MON, duration_minutes=15, earliest_minute=10 * 60, latest_minute=12 * 60)
    no_buf = min(run([a], cons([1], **kw)), key=lambda s: s.start)
    buf = min(run([a], cons([1], buffer_minutes=15, **kw)), key=lambda s: s.start)
    assert no_buf.start == at(MON, 11)
    assert buf.start == at(MON, 11, 15)
    # buffer applies before the block too
    before = run([person(1, busy=[(at(MON, 10), at(MON, 11))])], cons([1], buffer_minutes=15, end_date=MON, duration_minutes=15,
                                                                         earliest_minute=9 * 60, latest_minute=10 * 60 + 15))
    assert all(s.end <= at(MON, 9, 45) for s in before)


def test_time_of_day_and_weekday_filters():
    a = person(1)
    slots = run([a], cons([1], earliest_minute=14 * 60, latest_minute=16 * 60, weekdays=frozenset({2})))
    assert slots
    assert {s.start.weekday() for s in slots} == {2}
    assert all(at(s.start.date(), 14) <= s.start and s.end <= at(s.start.date(), 16) for s in slots)


def test_time_filters_are_in_requesters_zone():
    # Requester in New York asks for 10:00-12:00 local; London colleague's hours are 09-17 London = 04-12 NY
    nyc, lon = person(1, NYC), person(2, LON)
    slots = run([nyc, lon], cons([1, 2], earliest_minute=10 * 60, latest_minute=12 * 60), NYC)
    assert slots
    for s in slots:
        local = utc_to_local(s.start, NYC)
        assert 10 * 60 <= local.hour * 60 + local.minute and local.hour < 12


def test_morning_preference_ranks_mornings_first():
    slots = run([person(1)], cons([1], preference="morning", end_date=MON))
    top = max(slots, key=lambda s: s.score)
    assert top.start.hour < 12


def test_comfort_penalises_early_late_and_lunch():
    slots = run([person(1, start=7, end=20)], cons([1], end_date=MON, duration_minutes=60))
    by_start = {s.start.hour * 60 + s.start.minute: s.score for s in slots}
    assert by_start[10 * 60] > by_start[7 * 60]  # 07:00 start is before 09:00
    assert by_start[10 * 60] > by_start[12 * 60]  # lunch overlap
    assert by_start[10 * 60] > by_start[18 * 60]  # finishes after 18:00


def test_results_are_varied_non_overlapping_and_day_capped():
    c = cons([1], max_results=6, per_day_cap=2)
    slots = run([person(1)], c)
    assert len(slots) == 6
    days = [s.start.date() for s in slots]
    assert all(days.count(d) <= 2 for d in days)
    ordered = sorted(slots, key=lambda s: s.start)
    assert all(a.end <= b.start for a, b in zip(ordered, ordered[1:]))
    assert len(set(days)) >= 3


def test_past_times_are_never_offered():
    now = at(MON, 13)
    slots = find_slots({1: person(1)}, cons([1], end_date=MON), UTC, now)
    assert slots and all(s.start >= now for s in slots)


def test_dst_spring_forward_skips_nonexistent_local_times():
    assert local_to_utc(date(2026, 3, 8), 2 * 60 + 30, NYC) is None  # 02:30 doesn't exist
    assert local_to_utc(date(2026, 3, 8), 3 * 60, NYC) == datetime(2026, 3, 8, 7, 0)


def test_dst_fall_back_day_has_25_hours_and_search_works():
    day = date(2026, 11, 1)  # US clocks go back
    assert (local_to_utc(date(2026, 11, 2), 0, NYC) - local_to_utc(day, 0, NYC)) == timedelta(hours=25)
    ny = Person(1, "NY", NYC, 9 * 60, 17 * 60, frozenset(range(7)), [])
    c = Constraints([1], [], 60, day, day, max_results=100, per_day_cap=100)
    slots = find_slots({1: ny}, c, NYC, datetime(2026, 10, 30))
    assert slots
    assert all(9 <= utc_to_local(s.start, NYC).hour <= 16 for s in slots)
    assert utc_to_local(slots[0].start, NYC).hour == 9
