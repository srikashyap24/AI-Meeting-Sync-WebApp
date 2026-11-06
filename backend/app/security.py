import base64
import hashlib
import hmac
import ipaddress
import secrets
import socket
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

# ---------- passwords (PBKDF2-HMAC-SHA256) ----------

def hash_password(password: str, iterations: int | None = None) -> str:
    iterations = iterations or get_settings().pbkdf2_iterations
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(iters))
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False


# ---------- JWT (a "kind" claim keeps access tokens and OAuth state tokens non-interchangeable) ----------

ACCESS = "access"
OAUTH_STATE = "oauth_state"


def make_token(sub: int, kind: str, minutes: int, **extra) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(sub), "kind": kind, "iat": now, "exp": now + timedelta(minutes=minutes), **extra}
    return jwt.encode(payload, get_settings().secret_key, algorithm="HS256")


def decode_token(token: str, kind: str) -> dict:
    """Raises jwt.PyJWTError for bad signature, expiry, or wrong kind."""
    data = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"], options={"require": ["exp", "sub", "kind"]})
    if data.get("kind") != kind:
        raise jwt.InvalidTokenError("wrong token kind")
    return data


# ---------- encryption at rest ----------

def _fernet() -> Fernet:
    key = get_settings().encryption_key
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        # Any passphrase is accepted and stretched into a valid Fernet key.
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        raise ValueError("Stored token can't be decrypted (was ENCRYPTION_KEY changed?). Reconnect the calendar.")


# ---------- SSRF guard for user-supplied ICS URLs ----------

MAX_ICS_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 5


class UnsafeURL(ValueError):
    pass


def normalize_ics_url(url: str) -> str:
    url = url.strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    elif url.lower().startswith("webcals://"):
        url = "https://" + url[len("webcals://"):]
    return url


def _ip_is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return not (
        addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
        or addr.is_reserved or addr.is_unspecified
    )


def assert_public_url(url: str) -> str:
    """Validate scheme and that every address the host resolves to is public. Returns the normalised URL."""
    url = normalize_ics_url(url)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeURL("Only http, https and webcal URLs are allowed.")
    host = parts.hostname
    if not host:
        raise UnsafeURL("URL has no host.")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, ValueError):
        raise UnsafeURL("Could not resolve that host.")
    if not infos:
        raise UnsafeURL("Could not resolve that host.")
    for info in infos:
        if not _ip_is_public(info[4][0]):
            raise UnsafeURL("That address points to a private or internal network and is not allowed.")
    return url


def fetch_ics_text(url: str, client: httpx.Client | None = None) -> str:
    """GET an ICS feed with SSRF protection: each redirect hop is re-validated and the body is capped at 10 MB.

    Known limit: DNS is resolved once for validation and again by the HTTP client, so a hostile
    DNS server could rebind between the two. Pin the resolved IP or egress-filter in production.
    """
    own = client is None
    client = client or httpx.Client(timeout=15, follow_redirects=False)
    try:
        current = assert_public_url(url)
        for _ in range(MAX_REDIRECTS + 1):
            with client.stream("GET", current, headers={"Accept": "text/calendar, */*"}) as resp:
                if resp.is_redirect:
                    loc = resp.headers.get("location")
                    if not loc:
                        raise UnsafeURL("Redirect without a location.")
                    current = assert_public_url(urljoin(current, loc))
                    continue
                if resp.status_code >= 400:
                    raise ValueError(f"The calendar URL returned HTTP {resp.status_code}.")
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > MAX_ICS_BYTES:
                    raise ValueError("Calendar file is larger than 10 MB.")
                buf = bytearray()
                for chunk in resp.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > MAX_ICS_BYTES:
                        raise ValueError("Calendar file is larger than 10 MB.")
                return buf.decode("utf-8", errors="replace")
        raise UnsafeURL("Too many redirects.")
    except httpx.HTTPError as e:
        raise ValueError(f"Could not fetch the calendar: {e.__class__.__name__}")
    finally:
        if own:
            client.close()
