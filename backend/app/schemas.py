import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .timeutil import valid_zone


def _check_tz(v: str) -> str:
    if not valid_zone(v):
        raise ValueError("Unknown IANA time zone")
    return v


def _check_days(v: list[int]) -> list[int]:
    if any(d < 0 or d > 6 for d in v):
        raise ValueError("Weekdays must be 0 (Mon) to 6 (Sun)")
    return sorted(set(v))


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(min_length=1, max_length=120)
    timezone: str = "UTC"

    _tz = field_validator("timezone")(_check_tz)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    name: str
    timezone: str
    work_start: int
    work_end: int
    work_days: list[int]


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class ProfileIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = None
    work_start: int | None = Field(default=None, ge=0, le=24 * 60)
    work_end: int | None = Field(default=None, ge=0, le=24 * 60)
    work_days: list[int] | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v):
        return _check_tz(v) if v is not None else v

    @field_validator("work_days")
    @classmethod
    def _days(cls, v):
        return _check_days(v) if v is not None else v


class TeamIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class JoinIn(BaseModel):
    invite_code: str = Field(min_length=1, max_length=64)


class MemberOut(BaseModel):
    id: int
    name: str
    timezone: str
    work_start: int
    work_end: int
    work_days: list[int]


class TeamOut(BaseModel):
    id: int
    name: str
    invite_code: str
    members: list[MemberOut]


class CalendarOut(BaseModel):
    id: int
    kind: str
    name: str
    share_titles: bool
    external_account: str | None
    ics_url: str | None
    last_synced_at: str | None
    last_error: str | None
    event_count: int


class ManualCalIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class IcsUrlIn(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    name: str | None = Field(default=None, max_length=160)


class CalendarPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    share_titles: bool | None = None


class EventIn(BaseModel):
    title: str = Field(default="Busy", max_length=500)
    start: datetime  # ISO; no offset means UTC
    end: datetime
    all_day: bool = False
    location: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=5000)


class EventOut(BaseModel):
    id: int
    title: str
    start: str
    end: str
    all_day: bool
    location: str | None = None
    description: str | None = None


class ScheduleIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class SuggestIn(BaseModel):
    required_ids: list[int] = Field(default_factory=list, max_length=50)
    optional_ids: list[int] = Field(default_factory=list, max_length=50)
    duration_minutes: int = Field(default=30, ge=5, le=480)
    start_date: date | None = None
    end_date: date | None = None
    earliest: str = "00:00"
    latest: str = "24:00"
    weekdays: list[int] | None = None
    preference: str | None = None
    buffer_minutes: int = Field(default=0, ge=0, le=60)

    @field_validator("earliest", "latest")
    @classmethod
    def _hhmm(cls, v):
        if not re.fullmatch(r"\d{1,2}:\d{2}", v):
            raise ValueError("Use HH:MM")
        return v

    @field_validator("weekdays")
    @classmethod
    def _days(cls, v):
        return _check_days(v) if v is not None else v

    @field_validator("preference")
    @classmethod
    def _pref(cls, v):
        if v not in (None, "morning", "afternoon"):
            raise ValueError("preference must be morning or afternoon")
        return v


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=1000)


class QuickAddIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)  # earlier turns of THIS clarification only
