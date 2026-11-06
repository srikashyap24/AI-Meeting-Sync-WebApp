from app.security import OAUTH_STATE, hash_password, make_token, verify_password


def test_signup_login_me(client):
    r = client.post("/api/auth/signup", json={"email": "Sam@Demo-Meetsync.com", "password": "longenough1", "name": "Sam", "timezone": "Europe/Paris"})
    assert r.status_code == 201
    assert r.json()["user"]["email"] == "sam@demo-meetsync.com"
    assert r.json()["user"]["work_days"] == [0, 1, 2, 3, 4]
    r = client.post("/api/auth/login", json={"email": "sam@demo-meetsync.com", "password": "longenough1"})
    assert r.status_code == 200
    me = client.get("/api/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.json()["name"] == "Sam" and me.json()["timezone"] == "Europe/Paris"


def test_duplicate_wrong_password_and_validation(client):
    body = {"email": "a@demo-meetsync.com", "password": "longenough1", "name": "A", "timezone": "UTC"}
    assert client.post("/api/auth/signup", json=body).status_code == 201
    assert client.post("/api/auth/signup", json={**body, "email": "A@demo-meetsync.com"}).status_code == 409
    assert client.post("/api/auth/login", json={"email": "a@demo-meetsync.com", "password": "nope-nope-1"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "ghost@demo-meetsync.com", "password": "nope-nope-1"}).status_code == 401
    assert client.post("/api/auth/signup", json={**body, "email": "b@demo-meetsync.com", "password": "short"}).status_code == 422
    assert client.post("/api/auth/signup", json={**body, "email": "b@demo-meetsync.com", "timezone": "Mars/Base"}).status_code == 422
    assert client.post("/api/auth/signup", json={**body, "email": "b@example.test"}).status_code == 422  # reserved domain


def test_password_hash_is_salted_pbkdf2():
    h1, h2 = hash_password("secret-pass", 1000), hash_password("secret-pass", 1000)
    assert h1 != h2 and h1.startswith("pbkdf2_sha256$1000$")
    assert verify_password("secret-pass", h1) and not verify_password("other", h1)
    assert not verify_password("x", "garbage")


def test_protected_routes_need_a_valid_access_token(client, signup):
    u = signup("Sam")
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Bearer junk"}).status_code == 401
    # an oauth_state token must not work as an access token
    state = make_token(u.id, OAUTH_STATE, 10, provider="google")
    assert client.get("/api/me", headers={"Authorization": f"Bearer {state}"}).status_code == 401
    expired = make_token(u.id, "access", -1)
    assert client.get("/api/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_update_profile(client, signup):
    u = signup("Sam")
    r = client.patch("/api/me", headers=u.headers, json={"timezone": "Asia/Tokyo", "work_start": 600, "work_end": 1080, "work_days": [0, 2, 4]})
    assert r.status_code == 200 and r.json()["timezone"] == "Asia/Tokyo" and r.json()["work_days"] == [0, 2, 4]
    assert client.patch("/api/me", headers=u.headers, json={"work_start": 900, "work_end": 600}).status_code == 422
    assert client.patch("/api/me", headers=u.headers, json={"timezone": "Nope/Zone"}).status_code == 422


def test_unknown_email_login_does_the_same_hashing_work():
    from app.config import get_settings
    from app.routers.auth import dummy_hash

    assert dummy_hash().startswith(f"pbkdf2_sha256${get_settings().pbkdf2_iterations}$")
