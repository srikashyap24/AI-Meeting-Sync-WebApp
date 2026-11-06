from datetime import date

from app.services.nlp import RosterMember, rule_parse, sanitize

ROSTER = [RosterMember(1, "Me Self", "UTC"), RosterMember(2, "Anna Berg", "Europe/Berlin"), RosterMember(3, "Raj Patel", "Asia/Kolkata"),
          RosterMember(4, "Lucia Gomez", "America/Mexico_City")]
TODAY = date(2026, 11, 4)  # Wednesday


def parse(text):
    return sanitize(rule_parse(text, ROSTER, 1, TODAY), ROSTER, 1, TODAY)


def test_everyone_and_whole_team():
    for text in ("1 hour with everyone", "meeting with the whole team", "sync with everybody", "all of us for 30 min"):
        assert sorted(parse(text).required_ids) == [1, 2, 3, 4], text


def test_named_people_and_unknown_names():
    p = parse("45 minutes with Anna and Raj")
    assert sorted(p.required_ids) == [1, 2, 3] and p.duration_minutes == 45 and p.unknown_names == []
    p = parse("chat with Anna and Zed")
    assert p.required_ids == [1, 2] and p.unknown_names == ["Zed"]


def test_optional_attendees():
    p = parse("30 min with Anna, optionally Raj")
    assert p.required_ids == [1, 2] and p.optional_ids == [3]


def test_durations():
    assert parse("with Anna for 90 minutes").duration_minutes == 90
    assert parse("with Anna for 1.5 hours").duration_minutes == 90
    assert parse("with Anna for an hour").duration_minutes == 60
    assert parse("with Anna for half an hour").duration_minutes == 30
    assert parse("with Anna").duration_minutes == 30


def test_dates():
    p = parse("with Anna next week")
    assert (p.start_date, p.end_date) == (date(2026, 11, 9), date(2026, 11, 15))
    p = parse("with Anna tomorrow")
    assert p.start_date == p.end_date == date(2026, 11, 5)
    p = parse("with Anna next tuesday")
    assert p.start_date == p.end_date == date(2026, 11, 10)
    p = parse("with Anna")
    assert p.start_date == TODAY and (p.end_date - p.start_date).days == 13


def test_weekdays_allowed_and_excluded():
    assert parse("with Anna not Friday").weekdays == [0, 1, 2, 3, 5, 6]
    assert parse("with Anna except Monday or Friday").weekdays == [1, 2, 3, 5, 6]
    assert parse("with Anna on Tuesday or Thursday").weekdays == [1, 3]


def test_morning_afternoon_and_time_limits():
    assert parse("with Anna mornings").preference == "morning"
    assert parse("with Anna in the afternoon").preference == "afternoon"
    p = parse("with Anna after 2pm before 5pm")
    assert (p.earliest, p.latest) == ("14:00", "17:00")
    p = parse("with Anna between 10am and 12pm")
    assert (p.earliest, p.latest) == ("10:00", "12:00")


def test_full_example_from_the_brief():
    p = parse("45 minutes with Anna and Raj next week, mornings preferred, not Friday")
    assert sorted(p.required_ids) == [1, 2, 3] and p.duration_minutes == 45 and p.preference == "morning"
    assert 4 not in p.weekdays and 0 in p.weekdays and p.start_date == date(2026, 11, 9)


def test_sanitize_never_trusts_model_ids_or_values():
    raw = {"required_attendee_ids": [2, 999, "abc", 2, -5], "optional_attendee_ids": [2, 3, 12345],
           "duration_minutes": 100000, "start_date": "1999-01-01", "end_date": "2099-01-01", "earliest_time": "25:99",
           "latest_time": "nonsense", "weekdays": [0, 9, -1, "x"], "preference": "midnight", "buffer_minutes": -40,
           "unknown_names": ["Zed", {"evil": 1}]}
    p = sanitize(raw, ROSTER, 1, TODAY)
    assert p.required_ids == [1, 2] and p.optional_ids == [3]
    assert p.duration_minutes == 480 and p.buffer_minutes == 0 and p.preference is None
    assert p.start_date == TODAY and (p.end_date - p.start_date).days == 59
    assert p.weekdays == [0] and p.unknown_names == ["Zed"]
    assert (p.earliest, p.latest) == ("00:00", "24:00")  # unusable limits are ignored


def test_requester_is_always_required():
    p = sanitize({"required_attendee_ids": [3]}, ROSTER, 1, TODAY)
    assert p.required_ids == [1, 3]
