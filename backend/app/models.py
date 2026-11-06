from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .timeutil import utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    # minutes after local midnight; weekdays are Mon=0..Sun=6, comma separated
    work_start: Mapped[int] = mapped_column(Integer, default=9 * 60)
    work_end: Mapped[int] = mapped_column(Integer, default=17 * 60)
    work_days: Mapped[str] = mapped_column(String(20), default="0,1,2,3,4")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    calendars: Mapped[list["CalendarSource"]] = relationship(back_populates="user", cascade="all, delete-orphan")

    @property
    def work_days_list(self) -> list[int]:
        return [int(x) for x in self.work_days.split(",") if x != ""]


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    invite_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    memberships: Mapped[list["Membership"]] = relationship(back_populates="team", cascade="all, delete-orphan")


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("team_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    team: Mapped[Team] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship()


class CalendarSource(Base):
    __tablename__ = "calendar_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # google | microsoft | ics_url | ics_file | manual
    name: Mapped[str] = mapped_column(String(160))
    share_titles: Mapped[bool] = mapped_column(Boolean, default=False)
    ics_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ics_data: Mapped[str | None] = mapped_column(Text, nullable=True)  # uploaded file body
    external_account: Mapped[str | None] = mapped_column(String(320), nullable=True)
    enc_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    enc_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="calendars")
    events: Mapped[list["Event"]] = relationship(back_populates="calendar", cascade="all, delete-orphan")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    calendar_id: Mapped[int] = mapped_column(ForeignKey("calendar_sources.id", ondelete="CASCADE"), index=True)
    uid: Mapped[str | None] = mapped_column(String(512), nullable=True)
    title: Mapped[str] = mapped_column(String(500), default="")
    start_utc: Mapped[datetime] = mapped_column(DateTime, index=True)  # naive UTC
    end_utc: Mapped[datetime] = mapped_column(DateTime, index=True)  # naive UTC
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    # Owner-only details: never included in teammates' views, even when titles are shared.
    location: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    calendar: Mapped[CalendarSource] = relationship(back_populates="events")
