"""Google Calendar and Microsoft Graph clients: OAuth code flow, token refresh, event fetch."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

from ..config import get_settings
from ..security import decrypt, encrypt
from ..timeutil import iso_z, to_naive_utc, utcnow
from .ics import ParsedEvent

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_EVENTS = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
GOOGLE_PRIMARY = "https://www.googleapis.com/calendar/v3/calendars/primary"
GOOGLE_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"

MS_SCOPE = "offline_access Calendars.Read"
GRAPH = "https://graph.microsoft.com/v1.0"


def make_client() -> httpx.Client:
    """Single place to build the outbound HTTP client (tests swap this for a mock transport)."""
    return httpx.Client(timeout=20)


class ProviderError(Exception):
    pass


class NotConfigured(ProviderError):
    pass


@dataclass
class Tokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime  # naive UTC


def _ms_base() -> str:
    return f"https://login.microsoftonline.com/{get_settings().microsoft_tenant}/oauth2/v2.0"


def configured(provider: str) -> bool:
    s = get_settings()
    if provider == "google":
        return bool(s.google_client_id and s.google_client_secret)
    if provider == "microsoft":
        return bool(s.microsoft_client_id and s.microsoft_client_secret)
    return False


def authorize_url(provider: str, state: str) -> str:
    s = get_settings()
    if not configured(provider):
        raise NotConfigured(f"{provider.title()} sign-in isn't configured on this server.")
    if provider == "google":
        q = {"client_id": s.google_client_id, "redirect_uri": s.google_redirect_uri, "response_type": "code",
             "scope": GOOGLE_SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
             "include_granted_scopes": "true"}
        return f"{GOOGLE_AUTH}?{urlencode(q)}"
    q = {"client_id": s.microsoft_client_id, "redirect_uri": s.microsoft_redirect_uri, "response_type": "code",
         "scope": MS_SCOPE, "response_mode": "query", "state": state, "prompt": "select_account"}
    return f"{_ms_base()}/authorize?{urlencode(q)}"


def _token_request(provider: str, data: dict, client: httpx.Client) -> Tokens:
    s = get_settings()
    if provider == "google":
        url = GOOGLE_TOKEN
        data = {**data, "client_id": s.google_client_id, "client_secret": s.google_client_secret}
    else:
        url = f"{_ms_base()}/token"
        data = {**data, "client_id": s.microsoft_client_id, "client_secret": s.microsoft_client_secret,
                "scope": MS_SCOPE}
    r = client.post(url, data=data)
    if r.status_code != 200:
        raise ProviderError(f"{provider.title()} rejected the token request (HTTP {r.status_code}). Reconnect the calendar.")
    j = r.json()
    return Tokens(j["access_token"], j.get("refresh_token"), utcnow() + timedelta(seconds=int(j.get("expires_in", 3600))))


def exchange_code(provider: str, code: str, client: httpx.Client) -> Tokens:
    s = get_settings()
    redirect = s.google_redirect_uri if provider == "google" else s.microsoft_redirect_uri
    return _token_request(provider, {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect}, client)


def store_tokens(cal, tokens: Tokens) -> None:
    cal.enc_access_token = encrypt(tokens.access_token)
    if tokens.refresh_token:  # refresh responses often omit it; keep the old one
        cal.enc_refresh_token = encrypt(tokens.refresh_token)
    cal.token_expires_at = tokens.expires_at


def valid_access_token(cal, client: httpx.Client) -> str:
    """Return a usable access token, refreshing (and persisting) it when it's about to expire."""
    if not cal.enc_access_token:
        raise ProviderError("This calendar isn't connected. Reconnect it.")
    if cal.token_expires_at and cal.token_expires_at - utcnow() > timedelta(seconds=60):
        return decrypt(cal.enc_access_token)
    if not cal.enc_refresh_token:
        raise ProviderError("The connection expired and there's no refresh token. Reconnect the calendar.")
    tokens = _token_request(cal.kind, {"grant_type": "refresh_token", "refresh_token": decrypt(cal.enc_refresh_token)}, client)
    store_tokens(cal, tokens)
    return tokens.access_token


def account_info(provider: str, access_token: str, client: httpx.Client) -> tuple[str, str]:
    """(account label, default calendar name)."""
    h = {"Authorization": f"Bearer {access_token}"}
    if provider == "google":
        r = client.get(GOOGLE_PRIMARY, headers=h)
        if r.status_code == 200:
            j = r.json()
            return j.get("id", ""), j.get("summary") or "Google Calendar"
        return "", "Google Calendar"
    r = client.get(f"{GRAPH}/me/calendar", headers=h)
    if r.status_code == 200:
        j = r.json()
        return (j.get("owner") or {}).get("address", ""), j.get("name") or "Outlook Calendar"
    return "", "Outlook Calendar"


def _check(r: httpx.Response, provider: str) -> dict:
    if r.status_code in (401, 403):
        raise ProviderError(f"{provider.title()} denied access. Reconnect the calendar.")
    if r.status_code >= 400:
        raise ProviderError(f"{provider.title()} returned HTTP {r.status_code}.")
    return r.json()


def fetch_google(access_token: str, start: datetime, end: datetime, tz: ZoneInfo, client: httpx.Client) -> list[ParsedEvent]:
    h = {"Authorization": f"Bearer {access_token}"}
    params = {"timeMin": iso_z(start), "timeMax": iso_z(end), "singleEvents": "true", "orderBy": "startTime",
              "maxResults": 250}
    out: list[ParsedEvent] = []
    for _ in range(40):  # page cap
        j = _check(client.get(GOOGLE_EVENTS, headers=h, params=params), "google")
        for it in j.get("items", []):
            if it.get("status") == "cancelled" or it.get("transparency") == "transparent":
                continue
            if any(a.get("self") and a.get("responseStatus") == "declined" for a in it.get("attendees", [])):
                continue
            s, e = it.get("start", {}), it.get("end", {})
            if "date" in s:  # all-day
                sd = date.fromisoformat(s["date"])
                ed = date.fromisoformat(e["date"]) if "date" in e else sd + timedelta(days=1)
                su = to_naive_utc(datetime.combine(sd, time(0)), assume=tz)
                eu = to_naive_utc(datetime.combine(ed, time(0)), assume=tz)
                all_day = True
            elif "dateTime" in s:
                zone = ZoneInfo(s["timeZone"]) if s.get("timeZone") else tz
                su = to_naive_utc(datetime.fromisoformat(s["dateTime"].replace("Z", "+00:00")), assume=zone)
                eu = to_naive_utc(datetime.fromisoformat(e["dateTime"].replace("Z", "+00:00")), assume=zone)
                all_day = False
            else:
                continue
            if eu > su:
                out.append(ParsedEvent(it.get("id"), it.get("summary") or "", su, eu, all_day,
                                       (it.get("location") or "")[:500], (it.get("description") or "")[:5000]))
        token = j.get("nextPageToken")
        if not token:
            break
        params = {**params, "pageToken": token}
    return out


def fetch_microsoft(access_token: str, start: datetime, end: datetime, tz: ZoneInfo, client: httpx.Client) -> list[ParsedEvent]:
    h = {"Authorization": f"Bearer {access_token}", "Prefer": 'outlook.timezone="UTC"'}
    url: str | None = f"{GRAPH}/me/calendarView"
    params: dict | None = {"startDateTime": iso_z(start), "endDateTime": iso_z(end), "$top": 100,
                           "$select": "id,subject,start,end,isAllDay,isCancelled,showAs,responseStatus,location,bodyPreview"}
    out: list[ParsedEvent] = []
    for _ in range(40):
        j = _check(client.get(url, headers=h, params=params), "microsoft")
        for it in j.get("value", []):
            if it.get("isCancelled") or it.get("showAs") == "free":
                continue
            if (it.get("responseStatus") or {}).get("response") == "declined":
                continue
            s = datetime.fromisoformat(it["start"]["dateTime"][:19])
            e = datetime.fromisoformat(it["end"]["dateTime"][:19])
            if it.get("isAllDay"):
                # Graph reports all-day events as midnight-to-midnight; treat them as the owner's local days.
                su, eu, all_day = to_naive_utc(s, assume=tz), to_naive_utc(e, assume=tz), True
            else:
                su, eu, all_day = s, e, False  # already UTC thanks to the Prefer header
            if eu > su:
                out.append(ParsedEvent(it.get("id"), it.get("subject") or "", su, eu, all_day,
                                       ((it.get("location") or {}).get("displayName") or "")[:500], (it.get("bodyPreview") or "")[:5000]))
        url, params = j.get("@odata.nextLink"), None
        if not url:
            break
    return out
