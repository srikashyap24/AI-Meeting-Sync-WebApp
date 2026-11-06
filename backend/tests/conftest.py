import os

# Must be set before the app is imported: tests never touch a real key, DB or slow hashing.
os.environ.update({
    "DATABASE_URL": "sqlite://", "SECRET_KEY": "test-secret-key-for-pytest-only-0123456789",
    "ENCRYPTION_KEY": "test-encryption-passphrase", "PBKDF2_ITERATIONS": "1000", "ANTHROPIC_API_KEY": "", "OPENROUTER_API_KEY": "",  # env beats .env, so tests can never call a real API
    "FRONTEND_URL": "http://localhost:5173", "GOOGLE_CLIENT_ID": "gid", "GOOGLE_CLIENT_SECRET": "gsecret",
    "MICROSOFT_CLIENT_ID": "mid", "MICROSOFT_CLIENT_SECRET": "msecret",
})

from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import models  # noqa: E402,F401
from app.db import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    s = Session()
    yield s
    s.close()


@pytest.fixture()
def client(db_session):
    def _db():
        yield db_session

    app.dependency_overrides[get_db] = _db
    yield TestClient(app)
    app.dependency_overrides.clear()


class Who:
    def __init__(self, id, headers, email):
        self.id, self.headers, self.email = id, headers, email


@pytest.fixture()
def signup(client):
    def _signup(name="Alex", email=None, tz="UTC", work=None):
        email = email or f"{name.lower()}@demo-meetsync.com"
        r = client.post("/api/auth/signup", json={"email": email, "password": "correct-horse-1", "name": name, "timezone": tz})
        assert r.status_code == 201, r.text
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}
        if work:
            assert client.patch("/api/me", headers=h, json=work).status_code == 200
        return Who(r.json()["user"]["id"], h, email)

    return _signup


def next_monday() -> datetime:
    """00:00 UTC on the Monday of next week (always in the future, whatever today is)."""
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    return today + timedelta(days=7 - today.weekday())


def z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def add_busy(client):
    def _add(who, start: datetime, end: datetime, title="Busy", share=False, cal_id=None):
        if cal_id is None:
            cal_id = client.post("/api/calendars/manual", headers=who.headers, json={"name": "Mine"}).json()["id"]
            if share:
                client.patch(f"/api/calendars/{cal_id}", headers=who.headers, json={"share_titles": True})
        r = client.post(f"/api/calendars/{cal_id}/events", headers=who.headers,
                        json={"title": title, "start": z(start), "end": z(end)})
        assert r.status_code == 201, r.text
        return cal_id, r.json()["id"]

    return _add
