from datetime import timedelta

from .conftest import next_monday, z


def test_create_join_leave(client, signup):
    a, b = signup("Alice"), signup("Bob")
    t = client.post("/api/teams", headers=a.headers, json={"name": "Core"}).json()
    assert len(t["members"]) == 1 and t["invite_code"]
    j = client.post("/api/teams/join", headers=b.headers, json={"invite_code": t["invite_code"].lower()})
    assert j.status_code == 200 and {m["name"] for m in j.json()["members"]} == {"Alice", "Bob"}
    assert client.post("/api/teams/join", headers=b.headers, json={"invite_code": "NOPE"}).status_code == 404
    assert client.post(f"/api/teams/{t['id']}/leave", headers=b.headers).status_code == 204
    assert client.get(f"/api/teams/{t['id']}", headers=b.headers).status_code == 404
    assert [x["id"] for x in client.get("/api/teams", headers=a.headers).json()] == [t["id"]]


def test_outsiders_get_404_everywhere(client, signup):
    a, outsider = signup("Alice"), signup("Eve")
    t = client.post("/api/teams", headers=a.headers, json={"name": "Core"}).json()
    base = f"/api/teams/{t['id']}"
    mon = next_monday()
    assert client.get(base, headers=outsider.headers).status_code == 404
    assert client.get(f"{base}/busy?start={z(mon)}&end={z(mon + timedelta(days=1))}", headers=outsider.headers).status_code == 404
    assert client.post(f"{base}/schedule", headers=outsider.headers, json={"text": "30 min"}).status_code == 404
    assert client.post(f"{base}/suggest", headers=outsider.headers, json={}).status_code == 404
    assert client.post(f"{base}/leave", headers=outsider.headers).status_code == 404
    assert client.get("/api/teams", headers=outsider.headers).json() == []


def test_teammates_see_busy_unless_titles_shared(client, signup, add_busy):
    a, b = signup("Alice"), signup("Bob")
    t = client.post("/api/teams", headers=a.headers, json={"name": "Core"}).json()
    client.post("/api/teams/join", headers=b.headers, json={"invite_code": t["invite_code"]})
    mon = next_monday()
    cal_id, _ = add_busy(a, mon + timedelta(hours=10), mon + timedelta(hours=11), title="Dentist (private)")
    url = f"/api/teams/{t['id']}/busy?start={z(mon)}&end={z(mon + timedelta(days=1))}"

    def alice_events(viewer):
        members = client.get(url, headers=viewer.headers).json()["members"]
        return next(m for m in members if m["name"] == "Alice")["events"]

    assert [e["title"] for e in alice_events(b)] == ["Busy"]
    assert "Dentist" not in client.get(url, headers=b.headers).text
    assert [e["title"] for e in alice_events(a)] == ["Dentist (private)"]  # owners always see their own

    client.patch(f"/api/calendars/{cal_id}", headers=a.headers, json={"share_titles": True})
    assert [e["title"] for e in alice_events(b)] == ["Dentist (private)"]
    client.patch(f"/api/calendars/{cal_id}", headers=a.headers, json={"share_titles": False})
    assert [e["title"] for e in alice_events(b)] == ["Busy"]


def test_busy_window_validation(client, signup):
    a = signup("Alice")
    t = client.post("/api/teams", headers=a.headers, json={"name": "Core"}).json()
    mon = next_monday()
    assert client.get(f"/api/teams/{t['id']}/busy?start={z(mon)}&end={z(mon)}", headers=a.headers).status_code == 422
    assert client.get(f"/api/teams/{t['id']}/busy?start=garbage&end={z(mon)}", headers=a.headers).status_code == 422
