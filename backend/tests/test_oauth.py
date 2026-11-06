"""OAuth + provider sync against mocked Google/Microsoft endpoints. Live OAuth is NOT covered here."""
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.models import CalendarSource
from app.security import OAUTH_STATE, decrypt, make_token
from app.services import providers
from app.timeutil import utcnow


def day(n, h):
    return (datetime.utcnow() + timedelta(days=n)).replace(hour=h, minute=0, second=0, microsecond=0)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Provider:
    """A tiny fake of Google + Microsoft endpoints; records what was called."""

    def __init__(self):
        self.calls, self.token_grants = [], []
        self.events_status = 200
        self.issued = 0

    def handler(self, req: httpx.Request):
        url = str(req.url)
        self.calls.append((req.method, url.split("?")[0], req.headers.get("authorization")))
        if url.startswith(providers.GOOGLE_TOKEN) or "/oauth2/v2.0/token" in url:
            form = parse_qs(req.content.decode())
            self.token_grants.append(form["grant_type"][0])
            self.issued += 1
            body = {"access_token": f"access-{self.issued}", "expires_in": 3600}
            if form["grant_type"][0] == "authorization_code":
                body["refresh_token"] = "refresh-1"
            return httpx.Response(200, json=body)
        if url.startswith(providers.GOOGLE_PRIMARY) and "/events" not in url:
            return httpx.Response(200, json={"id": "me@gmail-demo.com", "summary": "Work"})
        if url.startswith(providers.GOOGLE_EVENTS):
            return httpx.Response(self.events_status, json={"items": [
                {"id": "ok", "summary": "Real meeting", "status": "confirmed", "start": {"dateTime": iso(day(2, 10))}, "end": {"dateTime": iso(day(2, 11))}},
                {"id": "cx", "summary": "Cancelled", "status": "cancelled", "start": {"dateTime": iso(day(2, 12))}, "end": {"dateTime": iso(day(2, 13))}},
                {"id": "free", "summary": "Free block", "transparency": "transparent", "start": {"dateTime": iso(day(2, 14))}, "end": {"dateTime": iso(day(2, 15))}},
                {"id": "dec", "summary": "Declined", "attendees": [{"self": True, "responseStatus": "declined"}], "start": {"dateTime": iso(day(2, 16))}, "end": {"dateTime": iso(day(2, 17))}},
                {"id": "ad", "summary": "Holiday", "start": {"date": day(3, 0).strftime("%Y-%m-%d")}, "end": {"date": day(4, 0).strftime("%Y-%m-%d")}},
            ]})
        if url.startswith(f"{providers.GRAPH}/me/calendar") and "calendarView" not in url:
            return httpx.Response(200, json={"name": "Calendar", "owner": {"address": "me@outlook-demo.com"}})
        if "calendarView" in url:
            assert 'outlook.timezone="UTC"' in req.headers["prefer"]
            f = lambda d: {"dateTime": d.strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"}
            return httpx.Response(200, json={"value": [
                {"id": "m1", "subject": "Board", "start": f(day(2, 9)), "end": f(day(2, 10)), "showAs": "busy", "isAllDay": False, "isCancelled": False, "responseStatus": {"response": "accepted"}},
                {"id": "m2", "subject": "FYI", "start": f(day(2, 11)), "end": f(day(2, 12)), "showAs": "free", "isCancelled": False},
                {"id": "m3", "subject": "Cancelled", "start": f(day(2, 13)), "end": f(day(2, 14)), "showAs": "busy", "isCancelled": True},
                {"id": "m4", "subject": "Declined", "start": f(day(2, 15)), "end": f(day(2, 16)), "showAs": "busy", "isCancelled": False, "responseStatus": {"response": "declined"}},
            ]})
        return httpx.Response(404)


@pytest.fixture()
def prov(monkeypatch):
    p = Provider()
    monkeypatch.setattr(providers, "make_client", lambda: httpx.Client(transport=httpx.MockTransport(p.handler)))
    return p


def location(r):
    return urlparse(r.headers["location"]), parse_qs(urlparse(r.headers["location"]).query)


def test_connect_url_has_offline_access_scope_and_signed_state(client, signup):
    u = signup("Sam")
    g = parse_qs(urlparse(client.get("/api/calendars/connect/google", headers=u.headers).json()["url"]).query)
    assert g["scope"] == ["https://www.googleapis.com/auth/calendar.readonly"] and g["access_type"] == ["offline"]
    assert g["response_type"] == ["code"]
    m = parse_qs(urlparse(client.get("/api/calendars/connect/microsoft", headers=u.headers).json()["url"]).query)
    assert "Calendars.Read" in m["scope"][0] and "offline_access" in m["scope"][0]
    assert client.get("/api/calendars/connect/dropbox", headers=u.headers).status_code == 404


def test_google_flow_encrypts_tokens_and_filters_events(client, signup, prov, db_session):
    u = signup("Sam")
    state = make_token(u.id, OAUTH_STATE, 10, provider="google")
    r = client.get(f"/api/oauth/google/callback?code=abc&state={state}", follow_redirects=False)
    loc, q = location(r)
    assert r.status_code == 302 and loc.netloc == "localhost:5173" and q["oauth"] == ["success"] and q["provider"] == ["google"]
    cal = db_session.query(CalendarSource).one()
    assert cal.kind == "google" and cal.external_account == "me@gmail-demo.com"
    assert cal.enc_access_token != "access-1" and "access-1" not in cal.enc_access_token
    assert decrypt(cal.enc_access_token) == "access-1" and decrypt(cal.enc_refresh_token) == "refresh-1"
    evs = client.get(f"/api/calendars/{cal.id}/events", headers=u.headers).json()
    assert sorted(e["title"] for e in evs) == ["Holiday", "Real meeting"]  # cancelled / free / declined skipped
    assert any(e["all_day"] for e in evs)
    # connecting the same account again updates it instead of duplicating
    client.get(f"/api/oauth/google/callback?code=abc&state={state}", follow_redirects=False)
    assert db_session.query(CalendarSource).count() == 1


def test_expired_token_is_refreshed_automatically_on_sync(client, signup, prov, db_session):
    u = signup("Sam")
    state = make_token(u.id, OAUTH_STATE, 10, provider="google")
    client.get(f"/api/oauth/google/callback?code=abc&state={state}", follow_redirects=False)
    cal = db_session.query(CalendarSource).one()
    cal.token_expires_at = utcnow() - timedelta(minutes=5)
    db_session.commit()
    prov.token_grants.clear(); prov.calls.clear()
    r = client.post(f"/api/calendars/{cal.id}/sync", headers=u.headers)
    assert r.status_code == 200 and r.json()["last_error"] is None and r.json()["event_count"] == 2
    assert prov.token_grants == ["refresh_token"]
    assert prov.calls[-1][2] == "Bearer access-2"  # the refreshed token was used
    db_session.refresh(cal)
    assert decrypt(cal.enc_access_token) == "access-2" and decrypt(cal.enc_refresh_token) == "refresh-1"  # refresh token kept


def test_provider_failure_is_reported_not_raised(client, signup, prov, db_session):
    u = signup("Sam")
    state = make_token(u.id, OAUTH_STATE, 10, provider="google")
    client.get(f"/api/oauth/google/callback?code=abc&state={state}", follow_redirects=False)
    cal = db_session.query(CalendarSource).one()
    prov.events_status = 401
    r = client.post(f"/api/calendars/{cal.id}/sync", headers=u.headers)
    assert r.status_code == 200 and "Reconnect" in r.json()["last_error"] and r.json()["event_count"] == 2  # old events kept


def test_microsoft_flow(client, signup, prov, db_session):
    u = signup("Sam")
    state = make_token(u.id, OAUTH_STATE, 10, provider="microsoft")
    r = client.get(f"/api/oauth/microsoft/callback?code=abc&state={state}", follow_redirects=False)
    assert location(r)[1]["oauth"] == ["success"]
    cal = db_session.query(CalendarSource).one()
    assert cal.kind == "microsoft" and cal.external_account == "me@outlook-demo.com"
    assert [e["title"] for e in client.get(f"/api/calendars/{cal.id}/events", headers=u.headers).json()] == ["Board"]


def test_bad_state_is_rejected(client, signup, prov, db_session):
    u = signup("Sam")
    wrong_kind = make_token(u.id, "access", 10, provider="google")
    wrong_provider = make_token(u.id, OAUTH_STATE, 10, provider="microsoft")
    expired = make_token(u.id, OAUTH_STATE, -1, provider="google")
    for st in (wrong_kind, wrong_provider, expired, "garbage", ""):
        r = client.get(f"/api/oauth/google/callback?code=abc&state={st}", follow_redirects=False)
        assert location(r)[1]["oauth"] == ["error"], st
    assert db_session.query(CalendarSource).count() == 0
    assert prov.calls == []  # no provider call was made with an untrusted state


def test_user_denying_consent(client, signup, prov):
    u = signup("Sam")
    state = make_token(u.id, OAUTH_STATE, 10, provider="google")
    r = client.get(f"/api/oauth/google/callback?error=access_denied&state={state}", follow_redirects=False)
    assert location(r)[1]["oauth"] == ["error"] and prov.calls == []
