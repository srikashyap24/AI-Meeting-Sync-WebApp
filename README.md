# MeetSync

See everyone's calendars in one place and let an AI find a time that works.

> 🚧 **Still under development.** Things work, but expect rough edges and missing features (see [Not done yet](#not-done-yet)).

## What it does

- **Groups:** after login, pick a group (or create / join one with an invite code).
- **Week calendar:** see who is doing what, on which day and time. Each person has a colour. Green = everyone is free.
- **Bring your calendars:** Google, Outlook, an ICS link or file, or add busy blocks by hand. Connect once and they show up in every group.
- **Privacy:** teammates only see "Busy" unless you turn on *Share event titles* for a calendar. Location and notes are never shared.
- **Find a time:** type *"45 minutes with Anna and Raj next week, mornings preferred, not Friday"* and get a few good options.
- **Add to my calendar:** type *"Dentist tomorrow 3-4pm"*. If something important is missing, it asks you.
- **Click any event** to see details. Edit or delete your own manual events right there.

## Run it

You need Python 3.12+ and Node 20+.

```bash
# Backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # fill in SECRET_KEY and ENCRYPTION_KEY (how is written in the file)
.venv/bin/python seed_demo.py   # optional demo team
.venv/bin/uvicorn app.main:app --port 8000

# Frontend (second terminal)
cd frontend
npm install
npm run dev                     # open http://localhost:5173
```

Demo login: `leo@demo-meetsync.com` / `demo-pass-123` (invite code `DEMO1234`).

## Settings (`backend/.env`)

| Variable | What it's for |
|---|---|
| `SECRET_KEY`, `ENCRYPTION_KEY` | Login tokens, and encrypting calendar tokens |
| `DATABASE_URL` | SQLite by default, Postgres supported |
| `FRONTEND_URL` | Allowed website address (CORS) and where OAuth returns |
| `GOOGLE_*`, `MICROSOFT_*` | Only needed to connect Google / Outlook |
| `OPENROUTER_API_KEY` | Optional. Powers "Add to my calendar" |
| `ANTHROPIC_API_KEY` | Optional. Powers "Find a time" |

No AI keys? Both features still work with a simpler built-in parser.

## Connect Google / Outlook

Set the redirect URI to `http://localhost:8000/api/oauth/google/callback` (or `.../microsoft/callback`).

- **Google:** Cloud console → enable *Google Calendar API* → OAuth consent screen (scope `calendar.readonly`, add test users) → create a *Web application* OAuth client → put the ID and secret in `.env`.
- **Microsoft:** Azure portal → App registrations → new *Web* app → API permission `Calendars.Read` (delegated) → create a client secret → put the ID, secret and tenant in `.env`.

## How "Find a time" works

The AI only reads your sentence. A normal program does the real work, so the AI never decides who is free.

1. The sentence becomes structured rules (people, length, dates, hours). Names are checked against your team.
2. The program tries every 15-minute slot. Everyone required must be free and inside **their own** working hours.
3. Slots are scored (preferred time, sooner is better, avoid very early, very late or lunch time) and the best few are shown.
4. If nothing fits, it tells you why in plain language.

## Tests

```bash
cd backend && .venv/bin/python -m pytest    # 106 tests
cd frontend && npm run build                # type-check and build
cd e2e && npm install && node run.mjs       # browser test (servers running, fresh seed)
```

## Not done yet

- Google / Outlook were tested against mock responses only, not live accounts.
- No creating real events or sending invites, no automatic background sync, no database migrations, no rate limiting or password reset.
- Quick add can't do recurring events ("every Monday") yet.
- Text you type into "Add to my calendar" goes to OpenRouter and its model providers. Leave the key empty to keep it local.

**Before putting this online:** set strong secrets, use Postgres and HTTPS, add rate limiting, and review the Google / Microsoft app settings.
