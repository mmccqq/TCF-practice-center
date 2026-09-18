"""Question bank endpoints backing the Task 2 / Task 3 pages.

Reads the four-layer model from "Core question set design.md", not the old
`questions` table:

    list_questions  one row per (task, month, question) - what a page shows
      -> fingerprints   the question's identity, and where its labels live
           -> themes, core_subjects   the controlled vocabulary

So a question that recurred in eight months is eight list rows sharing one
fingerprint, and therefore one set of labels. Under the old single table it was
one row whose month kept moving, which is what left 49 unlabelled orphans
behind.
"""
from __future__ import annotations

import math
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import CoreSubject, Fingerprint, ListQuestion, Theme
from ..schemas import (FrequentOut, FrequentSubject, FrequentTheme,
                       QuestionOut, QuestionPage, SubjectQuestion)

router = APIRouter(prefix="/api/questions", tags=["questions"])

SORTS = {
    # default: newest first. period is "YYYY-MM" so it sorts chronologically as
    # a string; the list row id breaks ties for a stable page boundary.
    "date_desc": (ListQuestion.period.desc(), ListQuestion.id.desc()),
    "date_asc": (ListQuestion.period.asc(), ListQuestion.id.asc()),
    # "most frequent" now means the question's whole history, not this month's
    # count - that is the number a candidate actually cares about
    "frequency": (Fingerprint.total_sightings.desc(), ListQuestion.period.desc()),
}


def _select():
    """The join every read goes through, and the columns QuestionOut needs."""
    return (
        select(ListQuestion.id, ListQuestion.f_id, ListQuestion.tache,
               ListQuestion.period, ListQuestion.month_sightings,
               Fingerprint.text, Fingerprint.abstract,
               Fingerprint.total_sightings, Fingerprint.months_seen,
               Fingerprint.first_seen, Fingerprint.last_seen,
               Theme.name.label("theme"), CoreSubject.name.label("core_subject"))
        # inner: a list row without a fingerprint is impossible (not-null FK)
        .join(Fingerprint, Fingerprint.id == ListQuestion.f_id)
        # outer: labelling lags scraping, so most questions have no theme yet
        .outerjoin(Theme, Theme.id == Fingerprint.theme_id)
        .outerjoin(CoreSubject, CoreSubject.id == Fingerprint.core_subject_id)
    )


def _out(row) -> QuestionOut:
    # year and month are derived, not stored: `period` is the single source of
    # truth for when, and two more columns holding the same fact could drift
    year, month = row.period.split("-")
    return QuestionOut(
        id=row.id, f_id=row.f_id, tache=row.tache, text=row.text,
        period=row.period, year=int(year), month=int(month),
        theme=row.theme, abstract=row.abstract, core_subject=row.core_subject,
        month_sightings=row.month_sightings,
        total_sightings=row.total_sightings, months_seen=row.months_seen,
        first_seen=row.first_seen, last_seen=row.last_seen,
    )


@router.get("", response_model=QuestionPage)
def list_questions(
    tache: int = Query(..., ge=2, le=3, description="2 or 3"),
    q: Optional[str] = Query(None, description="substring match on the text"),
    theme: Optional[str] = None,
    core_subject: Optional[str] = None,
    year: Optional[int] = None,
    period: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$"),
    sort: str = Query("date_desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> QuestionPage:
    if sort not in SORTS:
        raise HTTPException(422, f"sort must be one of {sorted(SORTS)}")

    where = [ListQuestion.tache == tache]
    if q:
        where.append(Fingerprint.text.ilike(f"%{q}%"))
    if theme:
        where.append(Theme.name == theme)
    if core_subject:
        where.append(CoreSubject.name == core_subject)
    if year:
        # period is "YYYY-MM", so a year is a prefix match - cheaper than
        # storing year separately and keeping the two in step
        where.append(ListQuestion.period.startswith(f"{year:04d}-"))
    if period:
        where.append(ListQuestion.period == period)

    # counted through the same joins, or a theme filter would count rows the
    # page does not return
    total = db.scalar(
        select(func.count()).select_from(ListQuestion)
        .join(Fingerprint, Fingerprint.id == ListQuestion.f_id)
        .outerjoin(Theme, Theme.id == Fingerprint.theme_id)
        .outerjoin(CoreSubject, CoreSubject.id == Fingerprint.core_subject_id)
        .where(*where)) or 0

    rows = db.execute(
        _select().where(*where).order_by(*SORTS[sort])
        .offset((page - 1) * per_page).limit(per_page)).all()

    # one indexed GROUP BY over the filtered set - the same joins again, so a
    # theme or search filter narrows these counts exactly as it narrows the rows
    period_counts = dict(db.execute(
        select(ListQuestion.period, func.count())
        .select_from(ListQuestion)
        .join(Fingerprint, Fingerprint.id == ListQuestion.f_id)
        .outerjoin(Theme, Theme.id == Fingerprint.theme_id)
        .outerjoin(CoreSubject, CoreSubject.id == Fingerprint.core_subject_id)
        .where(*where).group_by(ListQuestion.period)).all())

    return QuestionPage(
        items=[_out(r) for r in rows],
        period_counts=period_counts,
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

    `sources` is gone - it was provenance leaking into the API, and nothing in
    the UI used it once the source filter was removed.

    Two sets of numbers, because they answer different questions:

      counts   distinct questions - what the home page should advertise, since
               it promises a deduplicated archive. 3,463 Task 2 list entries
               are only 1,512 questions.
      entries  list rows, which is how many cards the list page will render.
    """
    per_tache = dict(
        db.execute(select(Fingerprint.tache, func.count())
                   .group_by(Fingerprint.tache)).all()
    )
    entries = dict(
        db.execute(select(ListQuestion.tache, func.count())
                   .group_by(ListQuestion.tache)).all()
    )
    scope = [ListQuestion.tache == tache] if tache else []
    periods = [p for (p,) in db.execute(
        select(ListQuestion.period).where(*scope).distinct()
        .order_by(ListQuestion.period.desc())).all()]
    # counted in list rows, not fingerprints: the number tells the user how many
    # entries the filter will show them
    themes = [{"name": n, "count": c} for n, c in db.execute(
        select(Theme.name, func.count())
        .select_from(ListQuestion)
        .join(Fingerprint, Fingerprint.id == ListQuestion.f_id)
        .join(Theme, Theme.id == Fingerprint.theme_id)
        .where(*scope)
        .group_by(Theme.name).order_by(func.count().desc(), Theme.name)).all()]
    return {
        "counts": {"tache2": per_tache.get(2, 0), "tache3": per_tache.get(3, 0)},
        "entries": {"tache2": entries.get(2, 0), "tache3": entries.get(3, 0)},
        "periods": periods,
        "themes": themes,
        "sorts": sorted(SORTS),
    }


# the default floor for "is this subject worth a card": below two distinct
# questions a core subject is a one-off, not something the exam keeps asking
MIN_QUESTIONS = 2


def build_core_set(db: Session, tache: int,
                   min_questions: int = MIN_QUESTIONS) -> tuple[list[FrequentTheme], int, int]:
    """(themes, labelled, total) for one task.

    Shared by the core-set page and the progress summary, so the two can never
    disagree about which subjects are in the set or which question represents
    one.

    Grouped in Python rather than SQL. The input is one row per labelled
    fingerprint - 544 for Task 2 - so the aggregation is trivial, and doing it
    here keeps "which question represents this subject" as three readable
    lines instead of a window function.

    Only labelled questions can appear. Task 2 has a core subject on 36% of its
    fingerprints and Task 3 on none, which is why `labelled` and `total` come
    back with the data: the page has to say what it is not showing.
    """
    rows = db.execute(
        select(Theme.name.label("theme"), CoreSubject.name.label("core_subject"),
               Fingerprint.id, Fingerprint.text, Fingerprint.total_sightings,
               Fingerprint.months_seen, Fingerprint.last_seen)
        .join(Fingerprint, Fingerprint.core_subject_id == CoreSubject.id)
        .join(Theme, Theme.id == CoreSubject.theme_id)
        .where(Fingerprint.tache == tache)).all()

    total = db.scalar(select(func.count()).select_from(Fingerprint)
                      .where(Fingerprint.tache == tache)) or 0

    by_subject: dict[tuple[str, str], list] = {}
    for r in rows:
        by_subject.setdefault((r.theme, r.core_subject), []).append(r)

    by_theme: dict[str, list[FrequentSubject]] = {}
    for (theme, subject), members in by_subject.items():
        if len(members) < min_questions:
            continue
        # most-sighted first, most recent breaking a tie, so questions[0] is
        # the representative - "the first one" by insertion order means nothing
        members.sort(key=lambda m: (-m.total_sightings, m.last_seen or "", m.id))
        by_theme.setdefault(theme, []).append(FrequentSubject(
            core_subject=subject,
            question_count=len(members),
            total_sightings=sum(m.total_sightings for m in members),
            questions=[SubjectQuestion(f_id=m.id, text=m.text,
                                       total_sightings=m.total_sightings,
                                       months_seen=m.months_seen,
                                       last_seen=m.last_seen)
                       for m in members],
            f_id=members[0].id,
        ))

    themes = [
        FrequentTheme(
            theme=name,
            question_count=sum(s.question_count for s in subjects),
            total_sightings=sum(s.total_sightings for s in subjects),
            subjects=sorted(subjects, key=lambda s: (-s.total_sightings,
                                                     -s.question_count,
                                                     s.core_subject)),
        )
        for name, subjects in by_theme.items()
    ]
    themes.sort(key=lambda t: (-t.total_sightings, t.theme))
    return themes, len(rows), total


@router.get("/frequent", response_model=FrequentOut)
def frequent_subjects(
    tache: int = Query(..., ge=2, le=3),
    min_questions: int = Query(MIN_QUESTIONS, ge=1,
                               description="only subjects with at least this "
                                           "many distinct questions"),
    db: Session = Depends(get_db),
) -> FrequentOut:
    """The core set: themes, their core subjects, every question in each."""
    themes, labelled, total = build_core_set(db, tache, min_questions)
    return FrequentOut(themes=themes, labelled=labelled, total=total)


@router.get("/{question_id}", response_model=QuestionOut)
def get_question(question_id: int, db: Session = Depends(get_db)) -> QuestionOut:
    """One list row. The id is a list_questions id, not the old scraper id."""
    row = db.execute(_select().where(ListQuestion.id == question_id)).first()
    if row is None:
        raise HTTPException(404, "question not found")
    return _out(row)


@router.get("/{question_id}/months", response_model=list[str])
def question_months(question_id: int, db: Session = Depends(get_db)) -> list[str]:
    """Every month this question was asked in - the "also asked in" line.

    Impossible to answer under the old model, which kept only the latest
    sighting of a recurring question.
    """
    f_id = db.scalar(select(ListQuestion.f_id).where(ListQuestion.id == question_id))
    if f_id is None:
        raise HTTPException(404, "question not found")
    return [p for (p,) in db.execute(
        select(ListQuestion.period).where(ListQuestion.f_id == f_id)
        .order_by(ListQuestion.period.desc())).all()]
