import logging
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import current_user, owned_calendar, owned_event
from ..models import CalendarSource, Event, User
from ..schemas import CalendarOut, CalendarPatch, EventIn, EventOut, IcsUrlIn, ManualCalIn, QuickAddIn
from ..security import MAX_ICS_BYTES, OAUTH_STATE, UnsafeURL, assert_public_url, decode_token, make_token
from ..services import providers, quickadd
from ..services.sync import SyncError, sync_calendar
from ..timeutil import get_zone, iso_z, to_naive_utc, utc_to_local, utcnow

log = logging.getLogger("meetsync.quickadd")
router = APIRouter(prefix="/api", tags=["calendars"])
PROVIDERS = ("google", "microsoft")


def cal_out(db: Session, c: CalendarSource) -> CalendarOut:
    n = db.query(func.count(Event.id)).filter(Event.calendar_id == c.id).scalar() or 0
    return CalendarOut(id=c.id, kind=c.kind, name=c.name, share_titles=c.share_titles, external_account=c.external_account,
                       ics_url=c.ics_url, last_synced_at=iso_z(c.last_synced_at) if c.last_synced_at else None,
                       last_error=c.last_error, event_count=n)


def ev_out(e: Event) -> EventOut:
    return EventOut(id=e.id, title=e.title, start=iso_z(e.start_utc), end=iso_z(e.end_utc), all_day=e.all_day,
                    location=e.location, description=e.description)


def _sync_or_report(db: Session, cal: CalendarSource, user: User):
    try:
        sync_calendar(db, cal, user)
    except SyncError:
        pass  # the error is stored on the calendar (last_error) and shown in the UI


@router.get("/calendars", response_model=list[CalendarOut])
def list_calendars(user: User = Depends(current_user), db: Session = Depends(get_db)):
    cals = db.query(CalendarSource).filter_by(user_id=user.id).order_by(CalendarSource.id).all()
    return [cal_out(db, c) for c in cals]


@router.post("/calendars/manual", response_model=CalendarOut, status_code=201)
def create_manual(body: ManualCalIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = CalendarSource(user_id=user.id, kind="manual", name=body.name.strip())
    db.add(c)
    db.commit()
    return cal_out(db, c)


@router.post("/calendars/ics-url", response_model=CalendarOut, status_code=201)
def add_ics_url(body: IcsUrlIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        url = assert_public_url(body.url)
    except UnsafeURL as e:
        raise HTTPException(400, str(e))
    c = CalendarSource(user_id=user.id, kind="ics_url", name=(body.name or "ICS calendar").strip(), ics_url=url)
    db.add(c)
    db.commit()
    _sync_or_report(db, c, user)
    return cal_out(db, c)


@router.post("/calendars/ics-upload", response_model=CalendarOut, status_code=201)
async def upload_ics(file: UploadFile = File(...), name: str | None = Form(default=None), user: User = Depends(current_user),
                     db: Session = Depends(get_db)):
    data = await file.read(MAX_ICS_BYTES + 1)
    if len(data) > MAX_ICS_BYTES:
        raise HTTPException(413, "Calendar file is larger than 10 MB")
    text = data.decode("utf-8", errors="replace")
    if "BEGIN:VCALENDAR" not in text:
        raise HTTPException(400, "That doesn't look like an .ics calendar file")
    label = (name or (file.filename or "Uploaded calendar").rsplit(".", 1)[0]).strip()[:160] or "Uploaded calendar"
    c = CalendarSource(user_id=user.id, kind="ics_file", name=label, ics_data=text)
    db.add(c)
    db.commit()
    try:
        sync_calendar(db, c, user)
    except SyncError as e:
        db.delete(c)
        db.commit()
        raise HTTPException(400, str(e))
    return cal_out(db, c)


@router.patch("/calendars/{cal_id}", response_model=CalendarOut)
def patch_calendar(cal_id: int, body: CalendarPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = owned_calendar(cal_id, user, db)
    if body.name is not None:
        c.name = body.name.strip()
    if body.share_titles is not None:
        c.share_titles = body.share_titles
    db.commit()
    return cal_out(db, c)


@router.post("/calendars/{cal_id}/sync", response_model=CalendarOut)
def sync_now(cal_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = owned_calendar(cal_id, user, db)
    _sync_or_report(db, c, user)
    return cal_out(db, c)


@router.delete("/calendars/{cal_id}", status_code=204)
def delete_calendar(cal_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = owned_calendar(cal_id, user, db)
    db.delete(c)
    db.commit()


# ---------- events (manual calendars are editable; others are read-only caches) ----------

@router.get("/calendars/{cal_id}/events", response_model=list[EventOut])
def list_events(cal_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = owned_calendar(cal_id, user, db)
    return [ev_out(e) for e in db.query(Event).filter_by(calendar_id=c.id).order_by(Event.start_utc).limit(1000)]


def _manual(cal_id: int, user: User, db: Session) -> CalendarSource:
    c = owned_calendar(cal_id, user, db)
    if c.kind != "manual":
        raise HTTPException(400, "Only manual calendars can be edited by hand")
    return c


def _times(body: EventIn):
    s, e = to_naive_utc(body.start), to_naive_utc(body.end)
    if e <= s:
        raise HTTPException(422, "Event must end after it starts")
    return s, e


@router.post("/calendars/{cal_id}/events", response_model=EventOut, status_code=201)
def add_event(cal_id: int, body: EventIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = _manual(cal_id, user, db)
    s, e = _times(body)
    ev = Event(calendar_id=c.id, title=body.title.strip() or "Busy", start_utc=s, end_utc=e, all_day=body.all_day,
               location=(body.location or "").strip() or None, description=(body.description or "").strip() or None)
    db.add(ev)
    db.commit()
    return ev_out(ev)


@router.put("/calendars/{cal_id}/events/{event_id}", response_model=EventOut)
def update_event(cal_id: int, event_id: int, body: EventIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = _manual(cal_id, user, db)
    ev = owned_event(c, event_id, db)
    ev.start_utc, ev.end_utc = _times(body)
    ev.title, ev.all_day = body.title.strip() or "Busy", body.all_day
    ev.location, ev.description = (body.location or "").strip() or None, (body.description or "").strip() or None
    db.commit()
    return ev_out(ev)


@router.delete("/calendars/{cal_id}/events/{event_id}", status_code=204)
def delete_event(cal_id: int, event_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = _manual(cal_id, user, db)
    db.delete(owned_event(c, event_id, db))
    db.commit()


# ---------- OAuth (Google / Microsoft) ----------

@router.get("/calendars/connect/{provider}")
def connect(provider: str, user: User = Depends(current_user)):
    """Returns the provider consent URL. The state is a short-lived signed JWT (kind=oauth_state)."""
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    state = make_token(user.id, OAUTH_STATE, 10, provider=provider)
    try:
        return {"url": providers.authorize_url(provider, state)}
    except providers.NotConfigured as e:
        raise HTTPException(501, str(e))


def _back(**params) -> RedirectResponse:
    return RedirectResponse(f"{get_settings().frontend_url.rstrip('/')}/?{urlencode(params)}", status_code=302)


@router.get("/oauth/{provider}/callback")
def oauth_callback(provider: str, code: str | None = Query(default=None), state: str | None = Query(default=None),
                   error: str | None = Query(default=None), db: Session = Depends(get_db)):
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    try:
        data = decode_token(state or "", OAUTH_STATE)
        if data.get("provider") != provider:
            raise jwt.InvalidTokenError("provider mismatch")
        user = db.get(User, int(data["sub"]))
    except (jwt.PyJWTError, ValueError):
        return _back(oauth="error", provider=provider, message="The sign-in link expired. Please try again.")
    if user is None:
        return _back(oauth="error", provider=provider, message="Account not found.")
    if error or not code:
        return _back(oauth="error", provider=provider, message=f"{provider.title()} sign-in was cancelled or denied.")
    try:
        with providers.make_client() as http:
            tokens = providers.exchange_code(provider, code, http)
            account, default_name = providers.account_info(provider, tokens.access_token, http)
            cal = None
            if account:
                cal = db.query(CalendarSource).filter_by(user_id=user.id, kind=provider, external_account=account).first()
            if cal is None:
                cal = CalendarSource(user_id=user.id, kind=provider, name=default_name, external_account=account or None)
                db.add(cal)
            providers.store_tokens(cal, tokens)
            db.commit()
            sync_calendar(db, cal, user, http)
    except SyncError as e:
        return _back(oauth="error", provider=provider, message=f"Connected, but the first sync failed: {e}")
    except providers.ProviderError as e:
        return _back(oauth="error", provider=provider, message=str(e))
    except Exception:
        return _back(oauth="error", provider=provider, message="Something went wrong connecting that account.")
    return _back(oauth="success", provider=provider)


# ---------- natural-language quick add ----------

QUICK_NAME = "Quick add"


@router.post("/me/quick-add")
def quick_add(body: QuickAddIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """'Dentist tomorrow 3-4pm' -> busy blocks on the caller's own 'Quick add' manual calendar (created on first use).
    If something important is missing the response is {"status": "question", ...}; the client sends the answer back
    with the conversation so far in `history` (at most MAX_QUESTIONS questions, then it adds with defaults)."""
    today = utc_to_local(utcnow(), get_zone(user.timezone)).date()
    history = [(t.role, t.content) for t in body.history]
    force = sum(1 for r, _ in history if r == "assistant") >= quickadd.MAX_QUESTIONS
    parser, turn = "rules", None
    if get_settings().openrouter_api_key:
        try:
            turn, parser = quickadd.llm_turn(body.text, user.timezone, today, history, force), "llm"
        except Exception as e:  # network, rate limit, bad JSON...: degrade to rules, never fail the request
            log.warning("LLM quick-add parse failed, using rules: %s", e)
    if turn is None:
        turn = quickadd.rule_turn([c for r, c in history if r == "user"], body.text, today, force)
    if turn["action"] == "ask" and not force:
        return {"status": "question", "question": turn["question"], "parser": parser, "events": [], "calendar_id": None, "notes": []}
    events, notes = quickadd.sanitize_events(turn["events"], user.timezone, today)
    if not events:
        return {"status": "none", "parser": parser, "events": [], "calendar_id": None, "notes": notes or [
            "I couldn't find a date and time in that. Try: “Dentist tomorrow 3-4pm” or “Offsite on Friday all day”."]}
    cal = db.query(CalendarSource).filter_by(user_id=user.id, kind="manual", name=QUICK_NAME).first()
    if cal is None:
        cal = CalendarSource(user_id=user.id, kind="manual", name=QUICK_NAME)
        db.add(cal)
        db.flush()
    rows = [Event(calendar_id=cal.id, title=e.title, start_utc=e.start, end_utc=e.end, all_day=e.all_day,
                  location=e.location, description=e.description) for e in events]
    db.add_all(rows)
    db.commit()
    return {"status": "added", "parser": parser, "calendar_id": cal.id, "notes": notes,
            "events": [{**ev_out(r).model_dump(), "date": e.local_date.isoformat()} for r, e in zip(rows, events)]}
