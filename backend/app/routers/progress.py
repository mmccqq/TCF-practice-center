"""Per-user question state: practised, bookmarked.

Both live on one `user_questions` row, because the bookmarks page needs both
at once and splitting them would mean reassembling one concept on every read.

Everything is keyed on the **fingerprint**. A question that recurred in eight
months is eight cards on the list page but one thing to practise or bookmark,
so acting on any card acts on all eight.

    GET    /api/progress?tache=2     both id sets, one request, for the list page
    PUT    /api/attempts/{f_id}      mark practised          (idempotent)
    DELETE /api/attempts/{f_id}
    PUT    /api/bookmarks/{f_id}     bookmark                (idempotent)
    DELETE /api/bookmarks/{f_id}
    GET    /api/bookmarks?tache=2    the bookmarks page, newest first
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models import CoreSubject, Fingerprint, Theme, User, UserQuestion, utcnow
from ..schemas import BookmarkOut, CoreSetProgress, ProgressOut
from .questions import build_core_set

router = APIRouter(tags=["progress"])


def _row(db: Session, user: User, f_id: int) -> Optional[UserQuestion]:
    return db.scalar(select(UserQuestion).where(UserQuestion.user_id == user.id,
                                                UserQuestion.f_id == f_id))


def _set_flag(db: Session, user: User, f_id: int, field: str, on: bool) -> Response:
    """Turn one flag on or off, creating and deleting the row as needed."""
    if on and not db.scalar(select(Fingerprint.id).where(Fingerprint.id == f_id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "question not found")

    row = _row(db, user, f_id)
    if row is None:
        if not on:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        row = UserQuestion(user_id=user.id, f_id=f_id)
        db.add(row)

    setattr(row, field, on)
    setattr(row, f"{field}_at", utcnow() if on else None)

    # a row with every flag off is the same as no row at all
    if not row.practiced and not row.bookmarked:
        db.delete(row)

    try:
        db.commit()
    except IntegrityError:
        # two clicks racing: uq_user_question_user_f caught the second, and the
        # first already recorded what this one wanted
        db.rollback()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/progress", response_model=ProgressOut)
def get_progress(
    tache: Optional[int] = Query(None, ge=2, le=3),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ProgressOut:
    """Both id sets in one request.

    Flat lists rather than pages: one integer per touched question, so the list
    page renders every card's state without a request per card.
    """
    q = select(UserQuestion.f_id, UserQuestion.practiced, UserQuestion.bookmarked)\
        .where(UserQuestion.user_id == user.id)
    if tache:
        q = q.join(Fingerprint, Fingerprint.id == UserQuestion.f_id)\
             .where(Fingerprint.tache == tache)
    rows = db.execute(q).all()
    return ProgressOut(
        practiced=[f for f, p, _ in rows if p],
        bookmarked=[f for f, _, b in rows if b],
    )


@router.get("/api/progress/summary", response_model=CoreSetProgress)
def core_set_progress(
    tache: int = Query(2, ge=2, le=3),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CoreSetProgress:
    """How far through the core set this user is.

    A separate endpoint from /api/progress so the home page and the task-page
    banner can show the number without pulling the whole core set, which is
    27 KB gzipped - worth it on the page that renders every card, wasteful for
    a one-line "12 of 86".
    """
    themes, _, _ = build_core_set(db, tache)
    reps = {s.f_id for t in themes for s in t.subjects}
    practised = {f for (f,) in db.execute(
        select(UserQuestion.f_id).where(UserQuestion.user_id == user.id,
                                        UserQuestion.practiced.is_(True))).all()}
    return CoreSetProgress(done=len(reps & practised), total=len(reps))


@router.put("/api/attempts/{f_id}", status_code=status.HTTP_204_NO_CONTENT)
def mark_practiced(f_id: int, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> Response:
    return _set_flag(db, user, f_id, "practiced", True)


@router.delete("/api/attempts/{f_id}", status_code=status.HTTP_204_NO_CONTENT)
def unmark_practiced(f_id: int, user: User = Depends(current_user),
                     db: Session = Depends(get_db)) -> Response:
    return _set_flag(db, user, f_id, "practiced", False)


@router.put("/api/bookmarks/{f_id}", status_code=status.HTTP_204_NO_CONTENT)
def add_bookmark(f_id: int, user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> Response:
    return _set_flag(db, user, f_id, "bookmarked", True)


@router.delete("/api/bookmarks/{f_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_bookmark(f_id: int, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> Response:
    return _set_flag(db, user, f_id, "bookmarked", False)


@router.get("/api/bookmarks", response_model=list[BookmarkOut])
def list_bookmarks(
    tache: Optional[int] = Query(None, ge=2, le=3),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[BookmarkOut]:
    """The bookmarks page: whole questions, newest bookmark first.

    Not paginated. A bookmark list is user-curated and self-limiting, so paging
    would be machinery for a page that will hold a few dozen rows. Revisit if
    anyone passes a few hundred.
    """
    rows = db.execute(
        select(UserQuestion.f_id, UserQuestion.bookmarked_at, UserQuestion.practiced,
               Fingerprint.tache, Fingerprint.text, Fingerprint.abstract,
               Fingerprint.last_seen, Fingerprint.months_seen,
               Fingerprint.total_sightings,
               Theme.name.label("theme"), CoreSubject.name.label("core_subject"))
        .join(Fingerprint, Fingerprint.id == UserQuestion.f_id)
        .outerjoin(Theme, Theme.id == Fingerprint.theme_id)
        .outerjoin(CoreSubject, CoreSubject.id == Fingerprint.core_subject_id)
        .where(UserQuestion.user_id == user.id, UserQuestion.bookmarked.is_(True))
        .where(*([Fingerprint.tache == tache] if tache else []))
        .order_by(UserQuestion.bookmarked_at.desc(), UserQuestion.f_id.desc())
    ).all()
    return [BookmarkOut(**r._mapping) for r in rows]
