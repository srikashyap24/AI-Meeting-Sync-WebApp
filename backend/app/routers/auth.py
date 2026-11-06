from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import current_user
from ..models import User
from ..schemas import LoginIn, ProfileIn, SignupIn, TokenOut, UserOut
from ..security import ACCESS, hash_password, make_token, verify_password

router = APIRouter(prefix="/api", tags=["auth"])

_dummy: str | None = None


def dummy_hash() -> str:
    """A hash at the configured cost, verified against when the email is unknown so timing doesn't reveal accounts."""
    global _dummy
    if _dummy is None or f"${get_settings().pbkdf2_iterations}$" not in _dummy:
        _dummy = hash_password("not-a-real-password")
    return _dummy


def user_out(u: User) -> UserOut:
    return UserOut(id=u.id, email=u.email, name=u.name, timezone=u.timezone, work_start=u.work_start,
                   work_end=u.work_end, work_days=u.work_days_list)


def _token(u: User) -> TokenOut:
    return TokenOut(access_token=make_token(u.id, ACCESS, get_settings().access_token_minutes), user=user_out(u))


@router.post("/auth/signup", response_model=TokenOut, status_code=201)
def signup(body: SignupIn, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "An account with that email already exists")
    u = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password), timezone=body.timezone)
    db.add(u)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "An account with that email already exists")
    return _token(u)


@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    u = db.query(User).filter(User.email == body.email.lower()).first()
    ok = verify_password(body.password, u.password_hash if u else dummy_hash())  # constant-ish time for unknown emails
    if not u or not ok:
        raise HTTPException(401, "Incorrect email or password")
    return _token(u)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user_out(user)


@router.patch("/me", response_model=UserOut)
def update_me(body: ProfileIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    data = body.model_dump(exclude_unset=True)
    ws = data.get("work_start", user.work_start)
    we = data.get("work_end", user.work_end)
    if ws is None or we is None or we <= ws:
        raise HTTPException(422, "Working hours must end after they start")
    for k in ("name", "timezone", "work_start", "work_end"):
        if data.get(k) is not None:
            setattr(user, k, data[k])
    if data.get("work_days") is not None:
        user.work_days = ",".join(str(d) for d in data["work_days"])
    db.commit()
    return user_out(user)
