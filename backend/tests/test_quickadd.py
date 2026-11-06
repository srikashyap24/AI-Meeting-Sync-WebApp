from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.config import get_settings
from app.routers import calendars as cal_router
from app.services import quickadd
from app.timeutil import parse_iso

FRI = date(2026, 11, 6)
NOW = datetime(2026, 11, 6, 7, 0)  # 12:30 in Kolkata


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(cal_router, "utcnow", lambda: NOW)


def test_rule_parser_phrases():
    r = lambda t: quickadd.rule_parse(t, FRI)
    assert r("Dentist tomorrow 3-4pm") == [{"title": "Dentist", "date": "2026-11-07", "all_day": False, "start": "15:00", "end": "16:00"}]
    assert r("gym on monday from 6pm to 7:30pm")[0]["date"] == "2026-11-09"
    assert r("lunch with Sam at 1pm on 12 nov for 45 minutes")[0]["end"] == "13:45"
    assert r("Offsite next friday all day")[0]["date"] == "2026-11-13" and r("Offsite next friday all day")[0]["all_day"]
    assert len(r("call mum today at 8pm; yoga on sunday at 7am")) == 2
    assert r("hello there") == []


def test_quick_add_converts_from_the_users_zone_to_utc(client, signup):
    u = signup("Raj", tz="Asia/Kolkata")
    r = client.post("/api/me/quick-add", headers=u.headers, json={"text": "Dentist tomorrow 3-4pm"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["parser"] == "rules" and len(j["events"]) == 1
    ev = j["events"][0]
    assert ev["title"] == "Dentist" and ev["date"] == "2026-11-07"
    assert (parse_iso(ev["start"]), parse_iso(ev["end"])) == (datetime(2026, 11, 7, 9, 30), datetime(2026, 11, 7, 10, 30))  # 15:00 IST
    cals = client.get("/api/calendars", headers=u.headers).json()
    assert [(c["name"], c["kind"], c["event_count"], c["share_titles"]) for c in cals] == [("Quick add", "manual", 1, False)]
    # a second request reuses the same calendar
    client.post("/api/me/quick-add", headers=u.headers, json={"text": "Gym on monday at 6pm"})
    assert [c["event_count"] for c in client.get("/api/calendars", headers=u.headers).json()] == [2]


def test_unparseable_text_creates_nothing_and_explains(client, signup):
    u = signup("Raj")
    j = client.post("/api/me/quick-add", headers=u.headers, json={"text": "hello there"}).json()
    assert j["events"] == [] and "Try:" in j["notes"][0]
    assert client.get("/api/calendars", headers=u.headers).json() == []


def test_added_events_show_in_team_view_as_busy_and_can_be_undone(client, signup):
    a, b = signup("Alice"), signup("Bob")
    t = client.post("/api/teams", headers=a.headers, json={"name": "T"}).json()
    client.post("/api/teams/join", headers=b.headers, json={"invite_code": t["invite_code"]})
    ev = client.post("/api/me/quick-add", headers=a.headers, json={"text": "Therapy tomorrow 3-4pm"}).json()
    url = f"/api/teams/{t['id']}/busy?start=2026-11-07T00:00:00Z&end=2026-11-08T00:00:00Z"
    seen = lambda who: next(m for m in client.get(url, headers=who.headers).json()["members"] if m["name"] == "Alice")["events"]
    assert [e["title"] for e in seen(b)] == ["Busy"]  # teammates never see the title by default
    assert [e["title"] for e in seen(a)] == ["Therapy"]
    # undo uses the normal event-delete route and is owner-only
    path = f"/api/calendars/{ev['calendar_id']}/events/{ev['events'][0]['id']}"
    assert client.delete(path, headers=b.headers).status_code == 404
    assert client.delete(path, headers=a.headers).status_code == 204
    assert seen(a) == []


def test_llm_output_is_validated_and_clamped(client, signup, monkeypatch):
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "k")
    bad = [{"title": "  A  very   spaced   title " + "x" * 400, "date": "2026-11-09", "start": "09:00", "end": "09:00"},   # zero length
           {"title": "Far", "date": "2099-01-01", "start": "09:00", "end": "10:00"},   # out of range
           {"title": "NoDate", "date": "someday", "start": "09:00", "end": "10:00"},
           {"title": "Long", "date": "2026-11-09", "start": "08:00", "end": "23:59"},
           {"title": "OnlyStart", "date": "2026-11-09", "start": "14:00"},          # end defaults to +1h
           "not a dict", {"title": "<script>", "date": "2026-11-10", "all_day": True}]
    monkeypatch.setattr(quickadd, "llm_turn", lambda *a, **k: {"action": "add", "question": "", "events": bad})
    u = signup("Raj", tz="Asia/Kolkata")
    j = client.post("/api/me/quick-add", headers=u.headers, json={"text": "whatever"}).json()
    assert j["parser"] == "llm"
    assert sorted(e["title"] for e in j["events"]) == ["<script>", "Long", "OnlyStart"]  # stored as plain text; the UI escapes it
    only = next(e for e in j["events"] if e["title"] == "OnlyStart")
    assert parse_iso(only["end"]) - parse_iso(only["start"]) == timedelta(hours=1)
    assert len(j["notes"]) == 3  # zero-length, out of range, no date; the non-dict item is dropped silently


def test_llm_failure_falls_back_to_rules(client, signup, monkeypatch):
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "k")

    def boom(*a, **k):
        raise ValueError("HTTP 429")

    monkeypatch.setattr(quickadd, "llm_turn", boom)
    u = signup("Raj", tz="Asia/Kolkata")
    j = client.post("/api/me/quick-add", headers=u.headers, json={"text": "Dentist tomorrow 3-4pm"}).json()
    assert j["parser"] == "rules" and len(j["events"]) == 1


def test_dst_and_all_day_in_the_users_zone():
    ev, notes = quickadd.sanitize_events([{"title": "Ghost", "date": "2027-03-14", "start": "02:30", "end": "03:30"}], "America/New_York", date(2027, 3, 1))
    assert ev == [] and "daylight" in notes[0]
    (day,), _ = quickadd.sanitize_events([{"title": "Off", "date": "2026-11-01", "all_day": True}], "America/New_York", date(2026, 10, 30))
    assert day.end - day.start == timedelta(hours=25)  # fall-back day


def test_llm_request_shape_and_model_fallback(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "openrouter_api_key", "sk-test")
    monkeypatch.setattr(s, "openrouter_model", "model-a:free, model-b:free")
    seen = []

    def handler(req: httpx.Request):
        import json
        body = json.loads(req.content)
        seen.append((str(req.url), req.headers["authorization"], body["model"], body["tool_choice"]))
        if body["model"] == "model-a:free":
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        args = json.dumps({"action": "add", "events": [{"title": "Dentist", "date": "2026-11-07", "start": "15:00", "end": "16:00"}]})
        return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [{"function": {"name": quickadd.TOOL_NAME, "arguments": args}}]}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        out = quickadd.llm_turn("dentist tomorrow 3-4pm", "UTC", FRI, [], False, c)
    assert out["action"] == "add" and out["events"][0]["title"] == "Dentist"
    assert [x[2] for x in seen] == ["model-a:free", "model-b:free"]
    assert seen[0][0] == "https://openrouter.ai/api/v1/chat/completions" and seen[0][1] == "Bearer sk-test"
    assert seen[0][3] == {"type": "function", "function": {"name": quickadd.TOOL_NAME}}  # forced tool call


def test_llm_accepts_plain_json_content_and_all_models_failing_raises():
    ok = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": 'Sure! {"events":[{"title":"X","date":"2026-11-07","start":"10:00"}]}'}}]})))
    assert quickadd.llm_turn("x", "UTC", FRI, [], False, ok)["events"][0]["title"] == "X"
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(ValueError):
        quickadd.llm_turn("x", "UTC", FRI, [], False, bad)


def test_requires_auth(client):
    assert client.post("/api/me/quick-add", json={"text": "x"}).status_code == 401


# ---------- clarifying questions ----------

def post(client, who, text, history=()):
    return client.post("/api/me/quick-add", headers=who.headers, json={"text": text, "history": list(history)}).json()


def test_rules_ask_only_when_date_or_time_is_missing(client, signup):
    u = signup("Raj", tz="Asia/Kolkata")
    q = post(client, u, "Dentist tomorrow")
    assert q["status"] == "question" and "time" in q["question"].lower() and q["events"] == []
    assert client.get("/api/calendars", headers=u.headers).json() == []  # nothing saved while a question is open
    done = post(client, u, "3pm", [{"role": "user", "content": "Dentist tomorrow"}, {"role": "assistant", "content": q["question"]}])
    assert done["status"] == "added" and done["events"][0]["title"] == "Dentist"
    assert parse_iso(done["events"][0]["start"]) == datetime(2026, 11, 7, 9, 30)  # 15:00 IST
    q2 = post(client, u, "Call the bank at 4pm")
    assert q2["status"] == "question" and "day" in q2["question"].lower()
    # complete or unrelated requests never trigger a question
    assert post(client, u, "Offsite friday all day")["status"] == "added"
    assert post(client, u, "hello there")["status"] == "none"


def test_questions_are_capped_then_it_stops_asking(client, signup):
    u = signup("Raj")
    hist = [{"role": "user", "content": "Dentist tomorrow"}, {"role": "assistant", "content": "What time should I put it at?"},
            {"role": "user", "content": "whenever"}, {"role": "assistant", "content": "What time should I put it at?"}]
    j = post(client, u, "no idea", hist)
    assert j["status"] == "none"  # two questions already asked: no third one


def test_llm_can_ask_and_the_conversation_is_sent_to_it(client, signup, monkeypatch):
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "k")
    seen = []

    def fake(text, tz, today, history, force, client=None):
        seen.append((text, list(history), force))
        if not history:
            return {"action": "ask", "question": "Is the meeting at 5 in the morning or the evening?", "events": []}
        return {"action": "add", "question": "", "events": [{"title": "Meeting", "date": "2026-11-09", "start": "17:00", "end": "18:00",
                                                              "location": "Room 4", "notes": "bring slides"}]}

    monkeypatch.setattr(quickadd, "llm_turn", fake)
    u = signup("Raj", tz="Asia/Kolkata")
    q = post(client, u, "meeting monday at 5")
    assert q["status"] == "question" and q["parser"] == "llm"
    hist = [{"role": "user", "content": "meeting monday at 5"}, {"role": "assistant", "content": q["question"]}]
    done = post(client, u, "evening", hist)
    assert done["status"] == "added" and done["events"][0]["location"] == "Room 4" and done["events"][0]["description"] == "bring slides"
    assert seen[1][1] == [("user", "meeting monday at 5"), ("assistant", q["question"])] and seen[1][2] is False
    # after two questions the model is told not to ask again, and a stray "ask" is not shown to the user
    full = hist + [{"role": "user", "content": "evening"}, {"role": "assistant", "content": "Which room?"}]
    monkeypatch.setattr(quickadd, "llm_turn", lambda *a, **k: {"action": "ask", "question": "Still unsure?", "events": []})
    assert post(client, u, "x", full)["status"] == "none"


def test_history_validation(client, signup):
    u = signup("Raj")
    big = [{"role": "user", "content": "x"}] * 13
    assert client.post("/api/me/quick-add", headers=u.headers, json={"text": "a", "history": big}).status_code == 422
    assert client.post("/api/me/quick-add", headers=u.headers, json={"text": "a", "history": [{"role": "system", "content": "x"}]}).status_code == 422


def test_turn_parsing_of_model_output():
    t = quickadd._turn_from({"tool_calls": [{"function": {"name": quickadd.TOOL_NAME, "arguments": '{"action":"ask","question":"  When?  "}'}}]})
    assert t == {"action": "ask", "question": "When?", "events": []}
    with pytest.raises(ValueError):
        quickadd._turn_from({"tool_calls": [{"function": {"name": quickadd.TOOL_NAME, "arguments": '{"action":"add","events":[]}'}}]})
