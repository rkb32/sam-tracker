"""Persistence. SQLite by default; set DATABASE_URL=postgresql://... to use Postgres."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Tracked(Base):
    __tablename__ = "tracked"
    solnum: Mapped[str] = mapped_column(String(64), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    snapshots: Mapped[list["Snapshot"]] = relationship(back_populates="tracked", order_by="Snapshot.id")


class Snapshot(Base):
    __tablename__ = "snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    solnum: Mapped[str] = mapped_column(ForeignKey("tracked.solnum"), index=True)
    taken_at: Mapped[str] = mapped_column(String(32))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    tracked: Mapped[Tracked] = relationship(back_populates="snapshots")


class Change(Base):
    __tablename__ = "changes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    solnum: Mapped[str] = mapped_column(ForeignKey("tracked.solnum"), index=True)
    from_snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id"))
    to_snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id"))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    critical: Mapped[bool] = mapped_column(Boolean, default=False)
    record: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)


def get_engine(url: str | None = None):
    return create_engine(url or os.environ.get("DATABASE_URL", "sqlite:///tracker.db"))


def init_db(engine) -> None:
    Base.metadata.create_all(engine)


def latest_snapshot(session: Session, solnum: str) -> Snapshot | None:
    stmt = select(Snapshot).where(Snapshot.solnum == solnum).order_by(Snapshot.id.desc()).limit(1)
    return session.scalars(stmt).first()
