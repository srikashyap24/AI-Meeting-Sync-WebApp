# MeetSync

Share calendars from different sources, see everyone's timetable in one place, and ask an assistant to find a meeting time.

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2 (SQLite by default, Postgres via `DATABASE_URL`), httpx, icalendar + recurring-ical-events, PyJWT, cryptography, anthropic SDK.
- **Frontend:** React + TypeScript (Vite). No UI or date libraries; all time zone maths uses `Intl`.

```
meetsync/
  backend/    FastAPI app, tests, seed_demo.py, .env.example
  frontend/   Vite + React + TS
  e2e/        Playwright script that drives the real UI
```

## Run it

Prerequisites: Python 3.12+, Node 20+.

```bash
# 1. Backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # then fill in SECRET_KEY and ENCRYPTION_KEY (commands are in the file)
.venv/bin/python seed_demo.py   # optional: demo team of 4 people in 4 time zones
.venv/bin/uvicorn app.main:app --port 8000

# 2. Frontend (new terminal)
cd frontend
npm install
npm run dev                     # http://localhost:5173, proxies /api to localhost:8000
```

After login you first choose a **group**; then the **Week** tab shows a calendar of who is doing what on which day and time (one colour per person, click a name to hide them, green = everyone free in working hours). Clicking a day header opens the **Day · working hours** view. Calendars belong to the user, so anything connected once appears in every group.

Log in with `leo@demo-meetsync.com` / `demo-pass-123` (also `anna@`, `ben@`, `raj@`). The team invite code is `DEMO1234`.
`seed_demo.py` is idempotent: re-running resets the demo data.

If `SECRET_KEY` / `ENCRYPTION_KEY` are unset the server generates throwaway ones at startup (and logs a warning), so sessions and stored OAuth tokens won't survive a restart.

### Tests

```bash
cd backend && .venv/bin/python -m pytest              # 106 tests, ~1s
cd frontend && npm run build                          # type-check + production build
cd e2e && npm install && npx playwright install chromium
cd e2e && node run.mjs                                # needs both servers running and a freshly seeded DB
```

### Configuration

All secrets come from the environment (or `backend/.env`). See `backend/.env.example`.

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Signs JWTs (access tokens and OAuth state tokens) |
| `ENCRYPTION_KEY` | Fernet key (or any passphrase) used to encrypt OAuth tokens at rest |
| `DATABASE_URL` | `sqlite:///./meetsync.db` by default; e.g. `postgresql+psycopg://…` (install `psycopg[binary]`) |
| `FRONTEND_URL` | The **only** CORS origin, and where OAuth returns the browser |
| `GOOGLE_*`, `MICROSOFT_*` | OAuth client credentials and redirect URIs (below) |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | Optional. Powers **Add to my calendar**. `OPENROUTER_MODEL` is a comma-separated list of chat models with tool calling, tried in order (free ones are rate-limited). Without a key a rule-based parser handles simple phrases |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | Optional. Without a key the rule-based parser and a templated recommendation are used |

## Connecting Google and Microsoft calendars

The redirect URI points at the **backend**; the backend then sends the browser back to `FRONTEND_URL` with `?oauth=success|error`.

### Google Calendar

1. In the [Google Cloud console](https://console.cloud.google.com/) create (or pick) a project.
2. **APIs & Services → Library**: enable **Google Calendar API**.
3. **OAuth consent screen**: choose *External*, fill in the app name and contact email, and add the scope `https://www.googleapis.com/auth/calendar.readonly`. While the app is in *Testing*, add every user's Google account under **Test users**.
4. **Credentials → Create credentials → OAuth client ID → Web application**. Add the authorised redirect URI `http://localhost:8000/api/oauth/google/callback` (use your real HTTPS URL in production).
5. Put the client ID and secret in `.env` as `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`, and set `GOOGLE_REDIRECT_URI` to the exact URI from step 4. Restart the backend.

Notes: the app requests `access_type=offline` and `prompt=consent` so Google returns a refresh token. Apps in *Testing* have refresh tokens that expire after 7 days. `calendar.readonly` is a sensitive scope, so a public launch needs Google's app verification.

### Microsoft 365 / Outlook

1. In the [Azure portal](https://portal.azure.com/) open **Microsoft Entra ID → App registrations → New registration**.
2. Choose supported account types (*Accounts in any organizational directory and personal Microsoft accounts* works with `MICROSOFT_TENANT=common`). Add a **Web** redirect URI `http://localhost:8000/api/oauth/microsoft/callback`.
3. **API permissions → Add a permission → Microsoft Graph → Delegated → `Calendars.Read`**. (`offline_access` is requested in the sign-in scope so a refresh token is issued.) Some organisations require an admin to grant consent.
4. **Certificates & secrets → New client secret**; copy the secret **Value** immediately.
5. Set `MICROSOFT_CLIENT_ID` (the *Application (client) ID*), `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_TENANT` (`common`, `organizations`, or your tenant ID) and `MICROSOFT_REDIRECT_URI`. Restart the backend.

If a provider isn't configured, its Connect button shows "…sign-in isn't configured on this server".

### Other sources

- **ICS link**: `http(s)://` or `webcal://`. Recurring events are expanded.
- **ICS file upload**: stored server-side so the Sync button can re-expand the window later.
- **Manual calendar**: add busy blocks by hand in the event editor.

For every source: cancelled events, events *you* declined, and "free"/transparent events are skipped. All-day events block the owner's local day; floating-time events are read in the owner's time zone. Sync window: 7 days back to 90 days ahead, on connect and on the Sync button.

## Add to my calendar (natural language)

The right-hand panel has a second tab. Type e.g. *"Dentist tomorrow 3-4pm"*, *"I'm on leave next Wednesday and Thursday"* or *"Lunch with Sam on 12 Nov at 1pm; call mum today at 8pm"*. The events are added to your own **Quick add** manual calendar (created on first use), the calendar jumps to that day, and each added item has an **Undo** button. Because calendars belong to you, they show up in every group; teammates see only "Busy" unless you turn on *Share event titles* for the Quick add calendar.

**Follow-up questions.** If something important is missing (no resolvable date, no start time on a timed event, or a truly ambiguous "at 5") the assistant asks one short question in the chat instead of guessing, and the answer completes the request. It never asks about optional details (duration defaults to 1 hour, title to "Busy"), and it stops after two questions and uses sensible defaults. Nothing is saved while a question is open; the client sends the conversation so far in `history` (the server keeps no chat state).

**Click an event** in the Week or Day view to open an expanded card: title, owner, date/time in your zone (plus the owner's local time if different), duration, location, notes, which calendar it comes from, what teammates can see, and who else is busy at the same time. Your own events on **manual** calendars (including everything added via Quick add) can be edited (title, date, time, all-day, location, notes) or deleted right there. Events synced from Google/Outlook/ICS are shown read-only (the next sync would overwrite edits). Teammates' events open read-only with only what the privacy rules allow. **Location and notes are owner-only** and are never sent to teammates, even when titles are shared.

How it works: `POST /api/me/quick-add` sends the text to an OpenRouter chat model with a *forced tool call* (it returns local date + HH:MM per event), and falls back to a rule-based parser if there's no key, the model is rate-limited, or it returns nothing usable. Whatever comes back is validated and clamped server-side before anything is stored (dates within about a year, ≤10 events, ≤24h each, titles trimmed to 200 chars, nonexistent DST times rejected) and converted from your time zone to UTC. Embedding models (like the `…/embeddings` endpoint) don't fit this job: they return vectors, not structured events.

## Privacy model

Teammates see only **Busy** unless the calendar owner turns on **Share event titles** for that calendar (per calendar). Masking happens on the server, so titles never reach a non-owner's browser. Owners always see their own titles. Teams are invisible to non-members (404), and every calendar/event route checks ownership (404 for other people's ids).

## How scheduling works ("Find a time")

```
text ──► parse ──► sanitize/clamp ──► availability engine ──► slots ──► recommendation
        (Claude     (server-side,      (pure Python,           (≤5,      (Claude, or
         forced      trust boundary)    no LLM)                 varied)   a template)
         tool call
         or rules)
```

1. **Parse.** With an API key, Claude is called with a *forced* tool call (`tool_choice` = `set_meeting_constraints`) and the team roster. It returns structured constraints: required/optional attendee ids, duration, date range, earliest/latest time (in the requester's zone), allowed weekdays, morning/afternoon preference, buffer, unknown names. Without a key, or if the API fails, a rule-based parser handles "everyone"/"whole team", names, "next week", "tomorrow", "next Tuesday", durations, weekdays (including "not Friday"), morning/afternoon, "after 2pm", "between 10 and 12", and "optionally X".
2. **Sanitize.** The model's output is never trusted: ids not on the team roster are dropped, the requester is always included, numbers are clamped (duration 5-480 min, buffer 0-60, range ≤ 60 days, nothing in the past), bad times/weekdays are discarded. If nobody is named, the whole team is assumed (and the UI says so).
3. **Search (deterministic).** The engine walks 15-minute candidate starts in the requester's zone across the date range. A candidate is valid only if **every required attendee** is inside their **own** working hours (their zone, their working weekdays) and has no busy block within `[start − buffer, end + buffer)`. DST gaps are skipped; fall-back days are handled with real elapsed time. **The LLM never decides what is free.**
4. **Score.** Base 100; +15 × (share of optional attendees who can attend); +20 for matching a morning/afternoon preference; −0.8 per day away from the start of the range (capped at 30 days); comfort penalties computed in *each attendee's* zone: starts before 09:00 or ends after 18:00 (−1 per 6 minutes) and lunch overlap 12:00-13:00 (−1 per 3 minutes).
5. **Pick.** Greedy best-first, no overlapping options, at most 2 per day, with a small demotion for days that already have a pick so results spread out.
6. **Explain.** Claude writes a 2-3 sentence recommendation from the already-computed slots (it is told to use only the facts given). Failure falls back to a template.
7. **Nothing found?** The response carries diagnostics: per person, how many candidates fall inside their working hours and how many of those are also free; whether working hours overlap at all across the zones; and whether they would overlap if the requester's time-of-day/weekday limits were removed. A plain-language explanation is built from these.

Endpoints: `POST /api/teams/{id}/schedule` (`{"text": "…"}`) and `POST /api/teams/{id}/suggest` (the same constraints as structured JSON; ids must be team members).

## Security notes

- ICS URLs: only `http`/`https`/`webcal`; every address the host resolves to must be public (private, loopback, link-local, multicast and reserved ranges are rejected, including IPv4-mapped IPv6); each redirect hop is re-validated (max 5); 10 MB cap enforced while streaming.
- JWTs carry a `kind` claim: `access` tokens and 10-minute signed `oauth_state` tokens are not interchangeable. The OAuth callback rejects tampered, expired, wrong-kind and wrong-provider state before making any provider call.
- OAuth access/refresh tokens are encrypted with Fernet at rest and refreshed automatically (60 s before expiry).
- Passwords: PBKDF2-HMAC-SHA256, 600k iterations, per-user salt. Login does equivalent work for unknown emails.
- CORS: only `FRONTEND_URL`. httpx request logging is lowered to WARNING so secret ICS URLs aren't logged.

## What was and wasn't tested

**Run and passing in this repo:**
- 106 pytest tests: availability engine (busy avoidance, working hours, cross-time-zone overlap, optional attendees, buffers, time/weekday filters, no-overlap diagnostic, DST), auth, team privacy (Busy vs shared titles, 404 for outsiders), the scheduling flow, the rule parser, ICS recurrence/transparency/cancelled/declined/all-day/floating/upload, SSRF blocking (including redirects and the size cap), cross-user access denial, and OAuth flows for Google and Microsoft **against mocked provider endpoints** (state validation, token encryption, auto-refresh, event filtering).
- `tsc --noEmit` and `vite build`.
- A Playwright run through the real UI against both servers with the seed data: login (and a failed login), timetable, masked vs shared titles, 24h toggle, "everyone free" checkboxes, asking for a meeting with the brief's example, clicking a slot (jumps to the day and highlights it), expanding local times, the no-slots diagnostic, the calendars modal (create a manual calendar, add an event, share toggle, remove), the SSRF refusal message, settings, OAuth return toasts, log out. Screenshots were inspected, which found and fixed real layout bugs.

**Also exercised live:** the OpenRouter quick-add path was called with the real key from the app UI (and directly): events were parsed correctly with a free Nemotron model; one run hit an empty answer and the fallback handled it. Tests never call it (`OPENROUTER_API_KEY` is blanked in the test environment).

**Not tested:**
- **Live Google and Microsoft OAuth / API calls.** No credentials were available. The flows are tested only against mocked endpoints, so request shapes and scopes follow the documented APIs but have not been exercised against the real services.
- **The live Claude API** (used by Find a time). No `ANTHROPIC_API_KEY` was available. The forced tool-call, sanitising and fallback behaviour are tested with a fake client; the real model's output quality, and whether the configured model id accepts these parameters, are unverified.
- Postgres (SQLite only), browsers other than headless Chromium, mobile layouts, and any load testing.

## Known limits

- Quick add text is sent to OpenRouter and the underlying model provider; free models may log prompts. Don't type anything you wouldn't want a third party to see, or leave the key unset to keep it local (rules only).
- Existing databases get the new `location`/`description` columns automatically at startup (a tiny add-missing-nullable-columns step, not a real migration tool).
- The event editor handles single-day events; a timed event that spans several days would be flattened to one day if edited.
- Quick add creates events only: no recurring events ("every Monday") and no editing/cancelling by sentence (use Undo, or click the event to edit or delete it).

- DNS is resolved once for validation and again by the HTTP client, so a hostile DNS server could in theory rebind between the two (ICS fetches). Pin the resolved IP or use egress filtering in production.
- No rate limiting or login lockout; no email verification or password reset.
- The JWT is kept in `localStorage` (readable by any XSS); there is no token revocation (tokens last 24 h by default).
- The schema is created with `create_all`; there are no migrations, so schema changes need a manual migration or a fresh DB.
- Sync only runs on connect and on the Sync button. A slow-drip ICS server is bounded only by the HTTP timeout.
- Only the primary Google/Outlook calendar is read. Events are cached per sync; edits made elsewhere don't appear until the next sync.
- Floating-time events and all-day events use the owner's *current* profile time zone; changing it doesn't re-interpret already-synced events until the next sync.
- "Everyone free" requires everyone counted to be inside their working hours, not just unbooked.
- The day view's axis is in the viewer's profile time zone, not the browser's.
- The scheduler considers a 60-day window and returns at most 5 options.

## Pre-deploy checklist

- [ ] Set strong, persistent `SECRET_KEY` and `ENCRYPTION_KEY` (never reuse the dev values); store them in a secrets manager. Rotating `ENCRYPTION_KEY` makes existing connections unreadable, so users must reconnect.
- [ ] Use Postgres (`DATABASE_URL`) with backups; decide on a migration tool (Alembic) before the first schema change.
- [ ] Serve over HTTPS only; set `FRONTEND_URL` and both OAuth redirect URIs to the real HTTPS origins and register them with Google/Microsoft.
- [ ] Complete Google app verification (sensitive scope) and move the consent screen out of *Testing*; grant Microsoft admin consent where required.
- [ ] Put the API behind a reverse proxy with rate limiting on `/api/auth/*` and `/api/calendars/ics-*`; consider egress filtering for the ICS fetcher.
- [ ] Redact query strings in access logs: the OAuth callback URL contains a one-time `code` and `state`.
- [ ] Set `ANTHROPIC_API_KEY` (optional), confirm the model id, and set a spend limit.
- [ ] Run more than one worker only after setting fixed secrets (ephemeral keys differ per process).
- [ ] Serve the built frontend (`npm run build` → `dist/`) from a static host or the reverse proxy, with the same-origin `/api` route.
- [ ] Review the CSP / security headers at the proxy; consider moving the JWT to an HTTP-only cookie.

## Next steps (out of scope for v1)

- Creating events and sending invites from a chosen slot
- Background / webhook-driven sync (Google push notifications, Graph subscriptions)
- A week heat-map view
- Database migrations (Alembic)
