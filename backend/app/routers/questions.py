"""Question bank endpoints backing the Task 2 / Task 3 pages."""
from __future__ import annotations

import math
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Question
from ..schemas import QuestionOut, QuestionPage

router = APIRouter(prefix="/api/questions", tags=["questions"])

SORTS = {
    # phase 0 default: newest first. period is "YYYY-MM" so it sorts
    # chronologically as a string; id breaks ties for a stable page boundary.
    "date_desc": (Question.period.desc(), Question.id.desc()),
    "date_asc": (Question.period.asc(), Question.id.asc()),
    "frequency": (Question.occurrences.desc(), Question.period.desc()),
}


@router.get("", response_model=QuestionPage)
def list_questions(
    tache: int = Query(..., ge=2, le=3, description="2 or 3"),
    q: Optional[str] = Query(None, description="substring match on the text"),
    source: Optional[str] = None,
    theme: Optional[str] = None,
    year: Optional[int] = None,
    period: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$"),
    sort: str = Query("date_desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> QuestionPage:
    if sort not in SORTS:
        raise HTTPException(422, f"sort must be one of {sorted(SORTS)}")

    where = [Question.tache == tache]
    if q:
        where.append(Question.text.ilike(f"%{q}%"))
    if source:
        where.append(Question.source == source)
    if theme:
        where.append(Question.theme == theme)
    if year:
        where.append(Question.year == year)
    if period:
        where.append(Question.period == period)

    total = db.scalar(select(func.count()).select_from(Question).where(*where)) or 0
    rows = db.scalars(
        select(Question).where(*where).order_by(*SORTS[sort])
        .offset((page - 1) * per_page).limit(per_page)
    ).all()

    return QuestionPage(
        items=[QuestionOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        per_page=per_page,
        pages=max(1, math.ceil(total / per_page)),
    )


@router.get("/meta")
def questions_meta(
    tache: Optional[int] = Query(None, ge=2, le=3,
                                 description="scope the filter options to one "
                                             "task; counts are always both"),
    db: Session = Depends(get_db),
) -> dict:
    """Counts and filter options, so the UI does not hardcode them.

    The options are scoped by `tache` because the two tasks do not share a
    theme vocabulary: offering Task 3's themes on the Task 2 page would list
    filters that can only ever return nothing.
    """
    per_tache = dict(
        db.execute(select(Question.tache, func.count()).group_by(Question.tache)).all()
    )
    scope = [Question.tache == tache] if tache else []
    sources = [s for (s,) in db.execute(
        select(Question.source).where(*scope).distinct()
        .order_by(Question.source)).all()]
    periods = [p for (p,) in db.execute(
        select(Question.period).where(*scope).distinct()
        .order_by(Question.period.desc())).all()]
    # (theme, count), commonest first: the order the filter list is drawn in,
    # and the count tells the user whether a theme is worth opening
    themes = [{"name": t, "count": n} for t, n in db.execute(
        select(Question.theme, func.count()).where(Question.theme.isnot(None), *scope)
        .group_by(Question.theme).order_by(func.count().desc(), Question.theme)).all()]
    return {
        "counts": {"tache2": per_tache.get(2, 0), "tache3": per_tache.get(3, 0)},
        "sources": sources,
        "periods": periods,
        "themes": themes,
        "sorts": sorted(SORTS),
    }


@router.get("/{question_id}", response_model=QuestionOut)
def get_question(question_id: str, db: Session = Depends(get_db)) -> QuestionOut:
    row = db.get(Question, question_id)
    if row is None:
        raise HTTPException(404, "question not found")
    return QuestionOut.model_validate(row)
