import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .db import get_db
from .models import CalendarSource, Event, Membership, Team, User
from .security import ACCESS, decode_token

bearer = HTTPBearer(auto_error=False)


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> User:
    if creds is None:
        raise HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    try:
        data = decode_token(creds.credentials, ACCESS)
        user = db.get(User, int(data["sub"]))
    except (jwt.PyJWTError, ValueError):
        user = None
    if user is None:
        raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    return user


def member_team(team_id: int, user: User, db: Session) -> Team:
    """Teams are invisible to non-members: 404, not 403."""
    team = db.get(Team, team_id)
    if team is None or not db.query(Membership).filter_by(team_id=team_id, user_id=user.id).first():
        raise HTTPException(404, "Team not found")
    return team


def owned_calendar(cal_id: int, user: User, db: Session) -> CalendarSource:
    cal = db.get(CalendarSource, cal_id)
    if cal is None or cal.user_id != user.id:
        raise HTTPException(404, "Calendar not found")
    return cal


def owned_event(cal: CalendarSource, event_id: int, db: Session) -> Event:
    ev = db.get(Event, event_id)
    if ev is None or ev.calendar_id != cal.id:
        raise HTTPException(404, "Event not found")
    return ev
