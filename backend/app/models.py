"""Phase 0 schema: users and the question bank.

Later phases attach to these: progress tracking (phase 2) hangs off
(user_id, question_id), themes (phase 2/3) off Question.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (Boolean, DateTime, ForeignKey, Index, Integer, String,
                        Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import List, Optional

from .db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    # null for Google-only accounts: they never set a password
    hashed_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    # "local" | "google" - which route created the account
    auth_provider: Mapped[str] = mapped_column(String(20), default="local")
    google_sub: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    attempts: Mapped[List["Attempt"]] = relationship(back_populates="user",
                                                     cascade="all, delete-orphan")


class Question(Base):
    __tablename__ = "questions"

    # the scrapers' 13-digit id: source|year|month|tache|partie|sujet
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    tache: Mapped[int] = mapped_column(Integer, index=True)
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), index=True)
    year: Mapped[int] = mapped_column(Integer, index=True)
    month: Mapped[int] = mapped_column(Integer)
    # "YYYY-MM", denormalised so the API can sort/filter by date without
    # recomputing it on every request
    period: Mapped[str] = mapped_column(String(7), index=True)
    partie: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sujet: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # how many raw scraped rows collapsed into this question (phase 2 uses it
    # for the high-frequency banks); 1 when loaded from un-deduplicated data
    occurrences: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        Index("ix_questions_tache_period", "tache", "period"),
    )


class Attempt(Base):
    """Phase 2 progress tracking. Created now so the schema does not need a
    breaking migration later; unused by the phase 0 UI."""

    __tablename__ = "attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    practiced: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True),
                                                    default=utcnow, onupdate=utcnow)

    user: Mapped["User"] = relationship(back_populates="attempts")

    __table_args__ = (UniqueConstraint("user_id", "question_id", name="uq_attempt_user_question"),)
