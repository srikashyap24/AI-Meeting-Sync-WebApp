import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, member_team
from ..models import CalendarSource, Event, Membership, Team, User
from ..schemas import JoinIn, MemberOut, ScheduleIn, SuggestIn, TeamIn, TeamOut
from ..services import scheduling
from ..timeutil import iso_z, parse_iso, utcnow

router = APIRouter(prefix="/api/teams", tags=["teams"])


def _member_out(u: User) -> MemberOut:
    return MemberOut(id=u.id, name=u.name, timezone=u.timezone, work_start=u.work_start, work_end=u.work_end,
                     work_days=u.work_days_list)


def _team_out(db: Session, t: Team) -> TeamOut:
    return TeamOut(id=t.id, name=t.name, invite_code=t.invite_code,
                   members=[_member_out(u) for u in scheduling.team_users(db, t)])


@router.get("", response_model=list[TeamOut])
def my_teams(user: User = Depends(current_user), db: Session = Depends(get_db)):
    teams = db.query(Team).join(Membership).filter(Membership.user_id == user.id).order_by(Team.name).all()
    return [_team_out(db, t) for t in teams]


@router.post("", response_model=TeamOut, status_code=201)
def create_team(body: TeamIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = Team(name=body.name.strip(), invite_code=secrets.token_hex(4).upper(), created_by=user.id)
    db.add(t)
    db.flush()
    db.add(Membership(team_id=t.id, user_id=user.id))
    db.commit()
    return _team_out(db, t)


@router.post("/join", response_model=TeamOut)
def join_team(body: JoinIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = db.query(Team).filter(Team.invite_code == body.invite_code.strip().upper()).first()
    if not t:
        raise HTTPException(404, "No team with that invite code")
    if not db.query(Membership).filter_by(team_id=t.id, user_id=user.id).first():
        db.add(Membership(team_id=t.id, user_id=user.id))
        db.commit()
    return _team_out(db, t)


@router.get("/{team_id}", response_model=TeamOut)
def get_team(team_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _team_out(db, member_team(team_id, user, db))


@router.post("/{team_id}/leave", status_code=204)
def leave_team(team_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = member_team(team_id, user, db)
    db.query(Membership).filter_by(team_id=t.id, user_id=user.id).delete()
    db.flush()
    if not db.query(Membership).filter_by(team_id=t.id).count():
        db.delete(t)  # last one out turns off the lights
    db.commit()


@router.get("/{team_id}/busy")
def team_busy(team_id: int, start: str = Query(...), end: str = Query(...), user: User = Depends(current_user),
              db: Session = Depends(get_db)):
    """Everyone's busy blocks in [start, end) (ISO). Titles are only shown to the owner or when the calendar shares them."""
    t = member_team(team_id, user, db)
    try:
        s, e = parse_iso(start), parse_iso(end)
    except ValueError:
        raise HTTPException(422, "start and end must be ISO timestamps")
    if e <= s or e - s > timedelta(days=31):
        raise HTTPException(422, "Window must be between 0 and 31 days")
    users = scheduling.team_users(db, t)
    rows = (db.query(Event, CalendarSource).join(CalendarSource, Event.calendar_id == CalendarSource.id)
            .filter(CalendarSource.user_id.in_([u.id for u in users]), Event.end_utc > s, Event.start_utc < e)
            .order_by(Event.start_utc).all())
    events: dict[int, list] = {u.id: [] for u in users}
    for ev, cal in rows:
        mine = cal.user_id == user.id
        visible = mine or cal.share_titles
        item = {"id": ev.id, "start": iso_z(ev.start_utc), "end": iso_z(ev.end_utc), "all_day": ev.all_day,
                "title": (ev.title or "Busy") if visible else "Busy", "shared": visible}
        if mine:  # location, notes and calendar details are for the owner only, even when titles are shared
            item.update(own=True, location=ev.location, description=ev.description, calendar_id=cal.id,
                        calendar_name=cal.name, calendar_kind=cal.kind, share_titles=cal.share_titles,
                        editable=cal.kind == "manual",
                        last_synced_at=iso_z(cal.last_synced_at) if cal.last_synced_at else None)
        events[cal.user_id].append(item)
    return {"members": [{**_member_out(u).model_dump(), "events": events[u.id]} for u in users]}


@router.post("/{team_id}/schedule")
def schedule(team_id: int, body: ScheduleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = member_team(team_id, user, db)
    return scheduling.schedule_from_text(db, t, user, body.text, utcnow())


@router.post("/{team_id}/suggest")
def suggest(team_id: int, body: SuggestIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = member_team(team_id, user, db)
    try:
        return scheduling.schedule_from_structured(db, t, user, body, utcnow())
    except ValueError as e:
        raise HTTPException(422, str(e))
