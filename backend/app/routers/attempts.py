"""Practice tracking: which questions a user has marked done.

Keyed on the **fingerprint**, not on a list row. A question that recurred in
eight months is eight cards on the list page but one thing to practise, so
marking any one of them marks all eight - see models.Attempt and step 5 of
"Core question set design.md".
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models import Attempt, Fingerprint, User

router = APIRouter(prefix="/api/attempts", tags=["attempts"])


@router.get("", response_model=list[int])
def list_attempts(
    tache: Optional[int] = Query(None, ge=2, le=3,
                                 description="only this task's questions"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[int]:
    """The fingerprint ids this user has marked done.

    A flat list rather than a page: it is one integer per practised question,
    so even a user who has worked through the whole bank sends a few thousand
    numbers once, and the list page can then render every card's state without
    a request per card.
    """
    q = select(Attempt.f_id).where(Attempt.user_id == user.id,
                                   Attempt.practiced.is_(True))
    if tache:
        q = q.join(Fingerprint, Fingerprint.id == Attempt.f_id).where(
            Fingerprint.tache == tache)
    return [f for (f,) in db.execute(q).all()]


@router.put("/{f_id}", status_code=status.HTTP_204_NO_CONTENT)
def mark_practiced(
    f_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Mark a question done. Idempotent - PUT, not POST, for that reason."""
    if not db.scalar(select(Fingerprint.id).where(Fingerprint.id == f_id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "question not found")

    row = db.scalar(select(Attempt).where(Attempt.user_id == user.id,
                                          Attempt.f_id == f_id))
    if row is None:
        db.add(Attempt(user_id=user.id, f_id=f_id, practiced=True))
        try:
            db.commit()
        except IntegrityError:
            # two clicks racing each other: uq_attempt_user_fingerprint caught
            # the second one, and the first already recorded what we wanted
            db.rollback()
    elif not row.practiced:
        row.practiced = True
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{f_id}", status_code=status.HTTP_204_NO_CONTENT)
def unmark_practiced(
    f_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Un-mark a question. Deletes the row rather than setting practiced=False:
    "never practised" and "un-marked" are the same state to a user, and keeping
    the distinction would mean explaining it somewhere."""
    row = db.scalar(select(Attempt).where(Attempt.user_id == user.id,
                                          Attempt.f_id == f_id))
    if row is not None:
        db.delete(row)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
