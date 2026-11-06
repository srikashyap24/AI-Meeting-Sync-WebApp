import socket

import httpx
import pytest

from app import security
from app.security import MAX_ICS_BYTES, UnsafeURL, assert_public_url, fetch_ics_text, normalize_ics_url

PUBLIC_IP = "93.184.216.34"


def fake_dns(monkeypatch, mapping=None):
    mapping = mapping or {}

    def getaddrinfo(host, port, *a, **k):
        ip = mapping.get(host)
        if ip is None:
            try:
                socket.inet_pton(socket.AF_INET6 if ":" in host else socket.AF_INET, host)
                ip = host
            except OSError:
                ip = PUBLIC_IP
        fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(fam, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(security.socket, "getaddrinfo", getaddrinfo)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/cal.ics", "http://localhost/cal.ics", "http://169.254.169.254/latest/meta-data", "http://10.0.0.5/x.ics",
    "http://192.168.1.10/x.ics", "http://172.16.0.1/x.ics", "http://[::1]/x.ics", "http://0.0.0.0/x.ics", "http://[::ffff:127.0.0.1]/x.ics",
    "webcal://127.0.0.1/x.ics",
])
def test_private_loopback_and_link_local_hosts_are_blocked(url, monkeypatch):
    fake_dns(monkeypatch, {"localhost": "127.0.0.1"})
    with pytest.raises(UnsafeURL):
        assert_public_url(url)


@pytest.mark.parametrize("url", ["ftp://example.com/x.ics", "file:///etc/passwd", "gopher://example.com", "javascript:alert(1)", "http://"])
def test_non_http_schemes_are_blocked(url, monkeypatch):
    fake_dns(monkeypatch)
    with pytest.raises(UnsafeURL):
        assert_public_url(url)


def test_hostname_resolving_to_private_ip_is_blocked(monkeypatch):
    fake_dns(monkeypatch, {"evil.example.com": "10.1.2.3"})
    with pytest.raises(UnsafeURL):
        assert_public_url("https://evil.example.com/x.ics")


def test_public_https_and_webcal_are_allowed_and_normalised(monkeypatch):
    fake_dns(monkeypatch)
    assert assert_public_url("webcal://calendar.example.com/a.ics") == "https://calendar.example.com/a.ics"
    assert normalize_ics_url("WEBCAL://x/y.ics") == "https://x/y.ics"
    assert assert_public_url("http://calendar.example.com/a.ics") == "http://calendar.example.com/a.ics"


def test_redirect_to_internal_address_is_blocked(monkeypatch):
    fake_dns(monkeypatch, {"internal.example.com": "10.0.0.9"})
    hits = []

    def handler(req: httpx.Request):
        hits.append(str(req.url))
        return httpx.Response(302, headers={"location": "http://internal.example.com/secret"})

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as c:
        with pytest.raises(UnsafeURL):
            fetch_ics_text("https://public.example.com/a.ics", c)
    assert hits == ["https://public.example.com/a.ics"]  # the internal hop was never requested


def test_safe_redirect_is_followed_and_loops_are_capped(monkeypatch):
    fake_dns(monkeypatch)

    def ok(req):
        if req.url.path == "/a.ics":
            return httpx.Response(301, headers={"location": "/b.ics"})
        return httpx.Response(200, text="BEGIN:VCALENDAR\nEND:VCALENDAR")

    with httpx.Client(transport=httpx.MockTransport(ok)) as c:
        assert "VCALENDAR" in fetch_ics_text("https://cal.example.com/a.ics", c)

    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(302, headers={"location": "/loop"}))) as c:
        with pytest.raises(UnsafeURL, match="redirect"):
            fetch_ics_text("https://cal.example.com/a.ics", c)


def test_response_is_capped_at_10mb(monkeypatch):
    fake_dns(monkeypatch)
    big = b"A" * (MAX_ICS_BYTES + 1)
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=big))) as c:
        with pytest.raises(ValueError, match="10 MB"):
            fetch_ics_text("https://cal.example.com/a.ics", c)
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, headers={"content-length": str(MAX_ICS_BYTES + 5)}, content=b"x"))) as c:
        with pytest.raises(ValueError, match="10 MB"):
            fetch_ics_text("https://cal.example.com/a.ics", c)


def test_endpoint_rejects_ssrf_targets(client, signup, monkeypatch):
    fake_dns(monkeypatch, {"localhost": "127.0.0.1"})
    u = signup("Sam")
    for url in ("http://127.0.0.1:8000/api/me", "http://localhost/x.ics", "http://169.254.169.254/", "file:///etc/passwd", "webcal://10.0.0.1/a.ics"):
        r = client.post("/api/calendars/ics-url", headers=u.headers, json={"url": url})
        assert r.status_code == 400, (url, r.text)
    assert client.get("/api/calendars", headers=u.headers).json() == []  # nothing was stored


def test_endpoint_accepts_webcal_and_syncs(client, signup, monkeypatch):
    from app.services import providers
    from datetime import datetime, timedelta

    fake_dns(monkeypatch)
    d = (datetime.utcnow() + timedelta(days=3)).strftime("%Y%m%d")
    body = (f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:1\r\nDTSTAMP:20260101T000000Z\r\nDTSTART:{d}T100000Z\r\n"
            f"DTEND:{d}T110000Z\r\nSUMMARY:Hi\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    monkeypatch.setattr(providers, "make_client", lambda: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body))))
    u = signup("Sam")
    r = client.post("/api/calendars/ics-url", headers=u.headers, json={"url": "webcal://cal.example.com/me.ics", "name": "Feed"})
    assert r.status_code == 201, r.text
    assert r.json()["ics_url"] == "https://cal.example.com/me.ics" and r.json()["event_count"] == 1 and r.json()["last_error"] is None


def test_tokens_are_encrypted_at_rest():
    enc = security.encrypt("ya29.secret-access-token")
    assert "secret" not in enc and security.decrypt(enc) == "ya29.secret-access-token"
