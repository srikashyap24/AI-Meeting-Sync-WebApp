from datetime import timedelta

import pytest

from .conftest import next_monday, z


@pytest.fixture()
def two(client, signup, add_busy):
    a, b = signup("Alice"), signup("Bob")
    mon = next_monday()
    cal_id, ev_id = add_busy(a, mon + timedelta(hours=10), mon + timedelta(hours=11), title="Secret")
    cal_b = client.post("/api/calendars/manual", headers=b.headers, json={"name": "Bobs"}).json()["id"]
    return a, b, cal_id, ev_id, cal_b


def test_other_users_cannot_touch_my_calendar(client, two):
    a, b, cal, ev, _ = two
    mon = next_monday()
    body = {"title": "x", "start": z(mon), "end": z(mon + timedelta(hours=1))}
    probes = [
        client.patch(f"/api/calendars/{cal}", headers=b.headers, json={"share_titles": True}),
        client.post(f"/api/calendars/{cal}/sync", headers=b.headers),
        client.delete(f"/api/calendars/{cal}", headers=b.headers),
        client.get(f"/api/calendars/{cal}/events", headers=b.headers),
        client.post(f"/api/calendars/{cal}/events", headers=b.headers, json=body),
        client.put(f"/api/calendars/{cal}/events/{ev}", headers=b.headers, json=body),
        client.delete(f"/api/calendars/{cal}/events/{ev}", headers=b.headers),
    ]
    assert [p.status_code for p in probes] == [404] * len(probes)
    # nothing changed
    assert client.get(f"/api/calendars/{cal}/events", headers=a.headers).json()[0]["title"] == "Secret"
    assert [c["share_titles"] for c in client.get("/api/calendars", headers=a.headers).json()] == [False]


def test_event_id_from_another_calendar_is_not_reachable_through_my_calendar(client, two):
    a, b, cal, ev, cal_b = two
    mon = next_monday()
    body = {"title": "hijack", "start": z(mon), "end": z(mon + timedelta(hours=1))}
    assert client.put(f"/api/calendars/{cal_b}/events/{ev}", headers=b.headers, json=body).status_code == 404
    assert client.delete(f"/api/calendars/{cal_b}/events/{ev}", headers=b.headers).status_code == 404
    assert client.get(f"/api/calendars/{cal}/events", headers=a.headers).json()[0]["title"] == "Secret"


def test_listing_only_returns_my_calendars(client, two):
    a, b, *_ = two
    assert [c["name"] for c in client.get("/api/calendars", headers=b.headers).json()] == ["Bobs"]
    assert [c["name"] for c in client.get("/api/calendars", headers=a.headers).json()] == ["Mine"]


def test_unauthenticated_requests_are_rejected(client, two):
    _, _, cal, ev, _ = two
    for method, url in [("get", "/api/calendars"), ("get", f"/api/calendars/{cal}/events"), ("post", f"/api/calendars/{cal}/sync"),
                        ("delete", f"/api/calendars/{cal}"), ("get", "/api/teams"), ("get", "/api/calendars/connect/google")]:
        assert getattr(client, method)(url).status_code == 401, url


def test_cors_only_allows_the_frontend_origin(client):
    ok = client.options("/api/me", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    bad = client.options("/api/me", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in bad.headers
