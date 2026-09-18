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
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow) # default = utcnow() will call the function immediately when the model is defined.

    questions: Mapped[List["UserQuestion"]] = relationship(
        back_populates="user", cascade="all, delete-orphan")


# --------------------------------------------------------------------------
# The four-layer question model (see "Core question set design.md").
#
#   raw_questions   one scraped sighting        provenance, internal only
#   fingerprints    one unique text             identity; labels live here
#   core_subjects   the controlled vocabulary   enforced by a foreign key
#   themes          the top-level buckets       enforced the same way
#
# Task 2 and Task 3 share these tables and are separated by a `tache` column,
# which is indexed and part of every unique constraint that needs it. They
# have identical shapes, so two sets of tables would be the same DDL written
# twice, two model classes each, and a third copy the day Task 1 is scraped.
#
# The vocabulary is per task even so: `themes.tache` with UNIQUE (tache, name)
# lets Task 3 have its own "Travel & tourism" with different core subjects
# under it. Without that column the two vocabularies would collide.
#
# Nothing reads these yet. `questions` stays as it is until step 6 of the
# migration plan.
# --------------------------------------------------------------------------


class Theme(Base):
    """One top-level bucket, from llm_tasks.VOCABULARY."""

    __tablename__ = "themes"

    id: Mapped[int] = mapped_column(primary_key=True)
    tache: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(40))

    core_subjects: Mapped[List["CoreSubject"]] = relationship(back_populates="theme")

    # unique per task, not globally: the two tasks are different question
    # types and may legitimately use the same theme name for different things
    __table_args__ = (UniqueConstraint("tache", "name", name="uq_theme_tache_name"),)


class CoreSubject(Base):
    """A finer label under a theme - the controlled vocabulary, as a table.

    A table rather than a string column so an unapproved label cannot be
    written at all. Task 2's vocabulary has 99 entries; the 587 labelled rows
    in `questions` carry 138 distinct core subjects, so 40 crept in unnoticed.
    That is the drift this prevents.

    No `tache` column: it is implied by the theme this belongs to.
    """

    __tablename__ = "core_subjects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    theme_id: Mapped[int] = mapped_column(ForeignKey("themes.id"), index=True)

    theme: Mapped["Theme"] = relationship(back_populates="core_subjects")

    # keyed on (theme, name), not name alone: the vocabulary is defined per
    # theme in llm_tasks.VOCABULARY, and the data already has one name
    # ("musical instrument") sitting under two different themes
    __table_args__ = (UniqueConstraint("theme_id", "name",
                                       name="uq_core_subject_theme_name"),)


class Fingerprint(Base):
    """One unique question text. The identity layer.

    Everything that is about the question rather than about one month points
    here: labels, and (from step 5) attempts. That is what makes a label
    consistent across every month the question recurs in, and what makes
    "tried" mean tried the question rather than tried September's copy.
    """

    __tablename__ = "fingerprints"

    # a surrogate id, deliberately not the hash itself. The hash would need no
    # allocation step, but it dead-ends at semantic merging: when two
    # differently-worded prompts are judged one question, a hash-as-id cannot
    # say so. With a surrogate that is a pointer change and no other foreign
    # key moves.
    id: Mapped[int] = mapped_column(primary_key=True)
    # the sha256 from deduplication.fingerprint()
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    tache: Mapped[int] = mapped_column(Integer, index=True)
    # canonical normalised wording, taken from the newest sighting
    text: Mapped[str] = mapped_column(Text)

    theme_id: Mapped[Optional[int]] = mapped_column(ForeignKey("themes.id"),
                                                    nullable=True, index=True)
    abstract: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    core_subject_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("core_subjects.id"), nullable=True, index=True)

    # derived from the raw rows, stored so the list page does not aggregate
    # thousands of rows on every request
    first_seen: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    last_seen: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    total_sightings: Mapped[int] = mapped_column(Integer, default=0)
    months_seen: Mapped[int] = mapped_column(Integer, default=0)

    theme: Mapped[Optional["Theme"]] = relationship()
    core_subject: Mapped[Optional["CoreSubject"]] = relationship()

    __table_args__ = (
        # the same wording under tache 2 and tache 3 is two questions, so the
        # hash alone is not unique - seed.py has always grouped on the pair
        UniqueConstraint("tache", "fingerprint", name="uq_fingerprint_tache_hash"),
    )


class RawQuestion(Base):
    """One scraped sighting, exactly as the scraper found it.

    Never reaches a user. It exists so counts can be recomputed, so the
    fingerprint and list layers can be rebuilt from the database rather than
    from JSONL files on a laptop, and so "where did this come from" has an
    answer.
    """

    __tablename__ = "raw_questions"

    # the scrapers' 13-digit id: source|year|month|tache|partie|sujet
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    # not null: a raw row without an identity is a bug, not a state
    f_id: Mapped[int] = mapped_column(ForeignKey("fingerprints.id"), index=True)
    tache: Mapped[int] = mapped_column(Integer, index=True)
    # unnormalised, unlike fingerprints.text - this is the evidence
    text: Mapped[str] = mapped_column(Text)
    period: Mapped[str] = mapped_column(String(7), index=True)
    partie: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sujet: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # internal only: "who reported this sighting", never returned by the API
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    fingerprint: Mapped["Fingerprint"] = relationship()

    __table_args__ = (
        # "every sighting of this question, newest month first"
        Index("ix_raw_questions_f_id_period", "f_id", "period"),
    )


class ListQuestion(Base):
    """One question per month - what the list page renders.

    Wholly derivable from raw_questions and fingerprints: group the raw rows by
    (tache, period, f_id). Materialised rather than a view so the list page's
    pagination and ordering stay cheap.

    This is the layer that fixes the orphan bug. Under the old single-table
    model a recurring question's canonical id moved to the newest month and the
    previous row was left behind, unlabelled; here a repeat is simply a second
    row in a second month, and both point at the same fingerprint, so they
    cannot disagree about their labels.
    """

    __tablename__ = "list_questions"

    # a surrogate, but the unique constraint below is what gives it meaning: a
    # rebuild matches on (tache, period, f_id) and upserts, so the same logical
    # row keeps the same id. A serial allocated fresh on every rebuild would
    # hand out new ids and break every bookmark pointing at one.
    id: Mapped[int] = mapped_column(primary_key=True)
    f_id: Mapped[int] = mapped_column(ForeignKey("fingerprints.id"), index=True)
    tache: Mapped[int] = mapped_column(Integer, index=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    # how many raw rows in *this* month collapsed here. Not the same number as
    # fingerprints.total_sightings, which counts every month - hence the
    # different name, so neither can be mistaken for the other.
    month_sightings: Mapped[int] = mapped_column(Integer, default=1)
    representative_raw_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("raw_questions.id"), nullable=True)

    fingerprint: Mapped["Fingerprint"] = relationship()

    __table_args__ = (
        # the database guarantees one row per question per month, rather than
        # the backfill remembering to
        UniqueConstraint("tache", "period", "f_id", name="uq_list_question_tache_period_f"),
        # how the list page reads it: one task, newest month first
        Index("ix_list_questions_tache_period", "tache", "period"),
    )


class UserQuestion(Base):
    """What one user has done with one question.

    One row per (user, question) holding every per-question flag, rather than a
    table per feature. The bookmarks page needs to know both whether a question
    is bookmarked and whether it is practised, and splitting those across two
    tables would mean reassembling one concept - "this user's relationship to
    this question" - on every read. Notes, ratings and spaced-repetition state
    belong here too when they arrive.

    Keyed on the **fingerprint**, not on a list row. A question that recurred in
    eight months is eight cards on the list page but one thing to practise, so
    marking any one of them marks all eight - see step 5 of "Core question set
    design.md".

    A row exists only while at least one flag is set; clearing the last one
    deletes it, because "never touched" and "un-marked" are the same state to a
    user and keeping the distinction would need explaining somewhere.
    """

    __tablename__ = "user_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         index=True)
    f_id: Mapped[int] = mapped_column(
        ForeignKey("fingerprints.id", ondelete="CASCADE"), index=True)

    practiced: Mapped[bool] = mapped_column(Boolean, default=False)
    practiced_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)

    bookmarked: Mapped[bool] = mapped_column(Boolean, default=False)
    # the bookmarks page orders by this, so it is a column rather than a
    # derivation from updated_at - which would change when the question was
    # practised and silently reorder the list
    bookmarked_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)

    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True),
                                                    default=utcnow, onupdate=utcnow)

    user: Mapped["User"] = relationship(back_populates="questions")

    __table_args__ = (
        UniqueConstraint("user_id", "f_id", name="uq_user_question_user_f"),
        # "my bookmarks, newest first" - the bookmarks page's only query
        Index("ix_user_questions_user_bookmarked", "user_id", "bookmarked_at"),
    )
