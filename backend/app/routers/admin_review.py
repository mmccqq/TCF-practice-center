"""Admin: review queues and run comparison.

review.html and compare.py, moved server-side.

Why move them
-------------
review.html kept decisions in localStorage, so clearing the browser lost the
adjudication and a second machine could not see it. compare.py printed to a
terminal and wrote a file you then had to feed back in by hand. Here a batch
lives in the database, decisions survive, and applying them writes straight to
`fingerprints` - no export/import round trip.

Uploads are JSON, not multipart: the browser parses the JSONL and posts rows,
which keeps python-multipart out of requirements and means one code path for
"a file" and "rows I already have".

Accepted row shapes, both produced by the existing tools:

    {"id": 1849, "theme": "Travel & tourism"}         one llm.py run
    {"id": 1849, "labels": {"gpt": "A", "ds": "B"}}   a compare.py disagreement
"""
from __future__ import annotations

import collections
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import current_admin
from ..db import get_db
from ..models import (CoreSubject, Fingerprint, RawQuestion, ReviewBatch,
                      ReviewItem, Theme, User, utcnow)

router = APIRouter(prefix="/api/admin", tags=["admin"],
                   dependencies=[Depends(current_admin)])

FIELDS = ("theme", "abstract", "core_subject")


def _key(name: str) -> str:
    """Same comparison form as load_labels.py, so a value that resolves here
    would resolve there too."""
    return " ".join(str(name).replace("’", "'").split()).casefold()


def _resolve_ids(db: Session, rows: list[dict]) -> dict[str, Optional[int]]:
    """Map each row's `id` to a fingerprint id.

    Accepts a fingerprint id or a 13-digit scraper id, exactly as
    load_labels.py does - label files made before the migration are keyed on
    the latter.
    """
    ids = {str(r.get("id")) for r in rows}
    out: dict[str, Optional[int]] = {}
    numeric = {int(i) for i in ids if i.isdigit() and len(i) < 12}
    known_fp = {f for (f,) in db.execute(
        select(Fingerprint.id).where(Fingerprint.id.in_(numeric))).all()} if numeric else set()
    by_scraper = dict(db.execute(
        select(RawQuestion.id, RawQuestion.f_id)
        .where(RawQuestion.id.in_(ids))).all())
    for i in ids:
        if i.isdigit() and len(i) < 12 and int(i) in known_fp:
            out[i] = int(i)
        else:
            out[i] = by_scraper.get(i)
    return out


# --------------------------------------------------------------------- batches


class BatchIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    tache: int = Field(2, ge=2, le=3)
    field: Optional[str] = None      # inferred from the rows when omitted
    rows: list[dict]


class DecisionIn(BaseModel):
    # "" is a real value meaning "skip this one", distinct from null/undecided
    decision: Optional[str] = None


def _infer_field(rows: list[dict]) -> str:
    found = {k for r in rows for k in r} & set(FIELDS)
    if len(found) == 1:
        return found.pop()
    if any("labels" in r for r in rows):
        # compare.py records which label the disagreement is about
        fields = {r.get("field") for r in rows} - {None}
        if len(fields) == 1:
            return fields.pop()
    raise HTTPException(422, f"cannot tell which label this file is about - "
                             f"expected one of {list(FIELDS)} per row, or a "
                             f"`field` alongside `labels`")


@router.post("/reviews", status_code=status.HTTP_201_CREATED)
def create_batch(payload: BatchIn, admin: User = Depends(current_admin),
                 db: Session = Depends(get_db)) -> dict:
    if not payload.rows:
        raise HTTPException(422, "no rows")
    field = payload.field or _infer_field(payload.rows)
    if field not in FIELDS:
        raise HTTPException(422, f"field must be one of {list(FIELDS)}")

    resolved = _resolve_ids(db, payload.rows)
    texts = dict(db.execute(
        select(Fingerprint.id, Fingerprint.text)
        .where(Fingerprint.id.in_([v for v in resolved.values() if v]))).all())

    batch = ReviewBatch(name=payload.name.strip(), field=field,
                        tache=payload.tache, created_by=admin.id)
    db.add(batch)
    db.flush()

    unmatched = 0
    for r in payload.rows:
        sid = str(r.get("id"))
        f_id = resolved.get(sid)
        if f_id is None:
            unmatched += 1
        candidates = r.get("labels") if isinstance(r.get("labels"), dict) else None
        if candidates is None:
            value = r.get(field)
            candidates = {r.get("model") or "run": value} if value is not None else {}
        db.add(ReviewItem(
            batch_id=batch.id, f_id=f_id, source_id=sid,
            text=texts.get(f_id) or r.get("text") or "",
            candidates={k: v for k, v in candidates.items() if v is not None},
        ))
    db.commit()
    return {"id": batch.id, "name": batch.name, "field": field,
            "items": len(payload.rows), "unmatched": unmatched}


@router.get("/reviews")
def list_batches(db: Session = Depends(get_db)) -> list[dict]:
    counts = dict(db.execute(
        select(ReviewItem.batch_id, func.count())
        .group_by(ReviewItem.batch_id)).all())
    decided = dict(db.execute(
        select(ReviewItem.batch_id, func.count())
        .where(ReviewItem.decision.isnot(None))
        .group_by(ReviewItem.batch_id)).all())
    return [{
        "id": b.id, "name": b.name, "field": b.field, "tache": b.tache,
        "created_at": b.created_at, "applied_at": b.applied_at,
        "items": counts.get(b.id, 0), "decided": decided.get(b.id, 0),
    } for b in db.scalars(select(ReviewBatch).order_by(ReviewBatch.id.desc())).all()]


@router.get("/reviews/{batch_id}")
def get_batch(batch_id: int, undecided: bool = Query(False),
              db: Session = Depends(get_db)) -> dict:
    b = db.get(ReviewBatch, batch_id)
    if b is None:
        raise HTTPException(404, "batch not found")
    q = select(ReviewItem).where(ReviewItem.batch_id == batch_id)
    if undecided:
        q = q.where(ReviewItem.decision.is_(None))
    items = db.scalars(q.order_by(ReviewItem.id)).all()
    return {
        "id": b.id, "name": b.name, "field": b.field, "tache": b.tache,
        "applied_at": b.applied_at,
        "items": [{
            "id": i.id, "f_id": i.f_id, "source_id": i.source_id,
            "text": i.text, "candidates": i.candidates, "decision": i.decision,
        } for i in items],
    }


@router.patch("/reviews/{batch_id}/items/{item_id}")
def decide(batch_id: int, item_id: int, payload: DecisionIn,
           db: Session = Depends(get_db)) -> dict:
    item = db.get(ReviewItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(404, "item not found")
    item.decision = payload.decision
    item.decided_at = utcnow() if payload.decision is not None else None
    db.commit()
    return {"id": item.id, "decision": item.decision}


@router.post("/reviews/{batch_id}/apply")
def apply_batch(batch_id: int, db: Session = Depends(get_db)) -> dict:
    """Write the decisions onto `fingerprints`.

    Names are resolved to ids here rather than at decision time, so a reviewer
    can record a label the vocabulary does not have yet. Those come back as
    `unresolved` with their counts - add them in the vocabulary tool, then
    apply again. Applying twice is safe: the second pass finds the values
    already set and reports them as unchanged.
    """
    b = db.get(ReviewBatch, batch_id)
    if b is None:
        raise HTTPException(404, "batch not found")

    theme_id = {(t.tache, _key(t.name)): t.id for t in db.scalars(select(Theme)).all()}
    cs_id = {(c.theme_id, _key(c.name)): c.id
             for c in db.scalars(select(CoreSubject)).all()}

    applied = unchanged = skipped = 0
    unresolved: collections.Counter = collections.Counter()
    for item in db.scalars(select(ReviewItem)
                           .where(ReviewItem.batch_id == batch_id)).all():
        if item.decision is None or item.decision == "" or item.f_id is None:
            skipped += 1
            continue
        f = db.get(Fingerprint, item.f_id)
        if f is None:
            skipped += 1
            continue

        if b.field == "abstract":
            new, column = item.decision.strip()[:200], "abstract"
        elif b.field == "theme":
            new, column = theme_id.get((f.tache, _key(item.decision))), "theme_id"
            if new is None:
                unresolved[f"theme {item.decision!r}"] += 1
                continue
        else:
            if f.theme_id is None:
                unresolved[f"core_subject {item.decision!r} (question has no theme)"] += 1
                continue
            new, column = cs_id.get((f.theme_id, _key(item.decision))), "core_subject_id"
            if new is None:
                unresolved[f"core_subject {item.decision!r}"] += 1
                continue

        if getattr(f, column) == new:
            unchanged += 1
        else:
            setattr(f, column, new)
            applied += 1

    b.applied_at = utcnow()
    db.commit()
    return {"applied": applied, "unchanged": unchanged, "skipped": skipped,
            "unresolved": dict(unresolved)}


@router.delete("/reviews/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_batch(batch_id: int, db: Session = Depends(get_db)) -> Response:
    b = db.get(ReviewBatch, batch_id)
    if b is None:
        raise HTTPException(404, "batch not found")
    db.delete(b)          # items go with it, via cascade
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------- compare


class CompareIn(BaseModel):
    """Several runs of the same task over the same questions."""

    field: Optional[str] = None
    runs: dict[str, list[dict]]      # name -> rows


@router.post("/compare")
def compare_runs(payload: CompareIn, db: Session = Depends(get_db)) -> dict:
    """Agreement between runs, and what they disagree about.

    The same numbers compare.py prints. Agreement is triage, not accuracy:
    where every run says the same thing the answer is usually right and not
    worth a human, and where they split is where a reviewer earns their time.
    """
    if len(payload.runs) < 2:
        raise HTTPException(422, "need at least two runs to compare")
    field = payload.field or _infer_field(
        [r for rows in payload.runs.values() for r in rows])

    # id -> {run: value}
    by_id: dict[str, dict[str, str]] = collections.defaultdict(dict)
    for run, rows in payload.runs.items():
        for r in rows:
            v = r.get(field)
            if v is not None:
                by_id[str(r.get("id"))][run] = str(v)

    names = list(payload.runs)
    shared = {i: vals for i, vals in by_id.items() if len(vals) == len(names)}
    agreed = {i: vals for i, vals in shared.items()
              if len({_key(v) for v in vals.values()}) == 1}
    disagreed = {i: vals for i, vals in shared.items() if i not in agreed}

    pairs: collections.Counter = collections.Counter()
    contested: collections.Counter = collections.Counter()
    for vals in disagreed.values():
        distinct = sorted({v for v in vals.values()}, key=_key)
        pairs[" | ".join(distinct)] += 1
        for v in distinct:
            contested[v] += 1

    resolved = _resolve_ids(db, [{"id": i} for i in disagreed])
    texts = dict(db.execute(
        select(Fingerprint.id, Fingerprint.text)
        .where(Fingerprint.id.in_([v for v in resolved.values() if v]))).all())

    return {
        "field": field,
        "runs": names,
        "compared": len(shared),
        "only_in_some": len(by_id) - len(shared),
        "agreed": len(agreed),
        "disagreed": len(disagreed),
        "agreement": round(len(agreed) / len(shared), 4) if shared else 0,
        "label_pairs": [{"pair": k, "count": v} for k, v in pairs.most_common()],
        "contested_labels": [{"label": k, "count": v}
                             for k, v in contested.most_common()],
        # ready to be posted straight back to /reviews as a batch
        "rows": [{"id": i, "field": field, "labels": vals,
                  "text": texts.get(resolved.get(i)) or ""}
                 for i, vals in disagreed.items()],
    }
