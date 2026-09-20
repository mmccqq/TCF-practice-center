"""Admin: run labelling jobs, import scraper output, export questions.

The three things that used to require a terminal.

    POST /api/admin/jobs            start a labelling run
    GET  /api/admin/jobs            progress of every run
    POST /api/admin/jobs/{id}/cancel
    GET  /api/admin/llm             which providers this server can actually use

    POST /api/admin/import          scraper JSONL -> the three question layers
    GET  /api/admin/export          questions as JSONL, for anything still local
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import llm_runner, scrape_runner
from ..auth import current_admin
from ..db import get_db
from ..models import (CoreSubject, Fingerprint, LlmJob, ListQuestion,
                      RawQuestion, Theme, User, utcnow)

router = APIRouter(prefix="/api/admin", tags=["admin"],
                   dependencies=[Depends(current_admin)])

PROJECT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT))


# ------------------------------------------------------------------- llm jobs


class RunIn(BaseModel):
    """One model to ask."""

    provider: str
    model: Optional[str] = None             # defaults to the provider's
    # Used for this run and then forgotten: not stored on the job, not logged,
    # not returned anywhere. Optional only when the server already has a key in
    # its environment.
    api_key: Optional[str] = None


class JobIn(BaseModel):
    """A labelling run.

    `mode` is what the admin chose, and it decides two things: how many models
    are asked, and where the finished job sends you.

        review    one model   -> Review, to accept or correct its answers
        compare   two models  -> Compare, to see where they split

    Either way the answers land in ONE batch, so applying it writes the agreed
    rows and the adjudicated ones together.
    """

    tache: int = Field(2, ge=2, le=3)
    task: str                               # theme | abstract | core_subject
    mode: str = "review"                    # review | compare
    runs: list[RunIn] = []
    chunk: int = Field(20, ge=1, le=100)
    limit: Optional[int] = Field(None, ge=1)
    theme_id: Optional[int] = None          # core_subject runs, one theme at a time
    # an explicit selection from the labelling page, which beats every filter
    f_ids: list[int] = []


@router.get("/llm")
def llm_capabilities() -> dict:
    """What this deployment can run. A provider whose key is missing here is
    missing on this server, whatever a laptop can do."""
    return llm_runner.available()


@router.post("/jobs", status_code=status.HTTP_201_CREATED)
def create_job(payload: JobIn, admin: User = Depends(current_admin),
               db: Session = Depends(get_db)) -> dict:
    caps = llm_runner.available()
    if payload.task not in caps["tasks"]:
        raise HTTPException(422, f"task must be one of {caps['tasks']}")
    if payload.mode not in ("review", "compare"):
        raise HTTPException(422, "mode must be review or compare")

    wanted = 2 if payload.mode == "compare" else 1
    if len(payload.runs) != wanted:
        raise HTTPException(422, f"{payload.mode} needs exactly {wanted} model"
                                 f"{'s' if wanted > 1 else ''}")

    runs = []
    for r in payload.runs:
        prov = next((p for p in caps["providers"] if p["name"] == r.provider), None)
        if prov is None:
            raise HTTPException(422, f"unknown provider {r.provider!r}")
        key = (r.api_key or "").strip() or None
        if not prov["configured"] and not key:
            raise HTTPException(409, f"{r.provider} needs an API key - paste one, "
                                     f"or set {' or '.join(prov['key_env'])} in the "
                                     f"server environment")
        runs.append({"provider": r.provider, "model": r.model or prov["default_model"],
                     "api_key": key})

    if payload.mode == "compare" and len({r["model"] for r in runs}) < 2:
        # two runs of the same model measure its consistency, not agreement -
        # and the batch would show one candidate, so nothing to compare
        raise HTTPException(422, "a comparison needs two different models")

    running = db.scalar(select(func.count()).select_from(LlmJob)
                        .where(LlmJob.status.in_(("queued", "running"))))
    if running:
        raise HTTPException(409, "a job is already running - wait for it or cancel it")

    scope = {k: v for k, v in (("limit", payload.limit),
                               ("theme_id", payload.theme_id),
                               ("f_ids", payload.f_ids or None)) if v}
    scope["mode"] = payload.mode
    # counted before anything is spent, so the answer to "how big is this"
    # arrives before the bill does
    rows = llm_runner.rows_for(db, payload.tache, payload.task, scope)
    if not rows:
        raise HTTPException(409, "no questions match - they may all be labelled "
                                 "already, or need a theme first")

    job = LlmJob(tache=payload.tache, task=payload.task,
                 provider=",".join(r["provider"] for r in runs),
                 model=" vs ".join(r["model"] for r in runs),
                 chunk=payload.chunk, scope=scope, status="queued",
                 total_rows=len(rows), created_by=admin.id)
    db.add(job)
    db.commit()
    llm_runner.start(job.id, runs)
    return _job_row(job)


def _job_row(j: LlmJob) -> dict:
    return {
        "id": j.id, "kind": j.kind, "tache": j.tache, "task": j.task,
        "provider": j.provider,
        "model": j.model, "chunk": j.chunk, "scope": j.scope, "status": j.status,
        "total_rows": j.total_rows, "total_chunks": j.total_chunks,
        "done_chunks": j.done_chunks, "answered": j.answered, "error": j.error,
        "batch_id": j.batch_id, "created_at": j.created_at,
        "finished_at": j.finished_at,
    }


@router.get("/jobs")
def list_jobs(db: Session = Depends(get_db)) -> list[dict]:
    return [_job_row(j) for j in db.scalars(
        select(LlmJob).order_by(LlmJob.id.desc()).limit(25)).all()]


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, db: Session = Depends(get_db)) -> dict:
    j = db.get(LlmJob, job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    if j.status in ("done", "failed", "cancelled", "interrupted"):
        return _job_row(j)
    # the runner checks this between chunks; the one in flight still completes,
    # because abandoning a paid-for call would waste it
    j.status, j.finished_at = "cancelled", utcnow()
    db.commit()
    return _job_row(j)


@router.get("/questions/preview")
def preview_scope(tache: int = Query(2, ge=2, le=3), task: str = Query(...),
                  theme_id: Optional[int] = None, limit: Optional[int] = None,
                  db: Session = Depends(get_db)) -> dict:
    """How many questions a job would cover, before starting it."""
    scope = {k: v for k, v in (("limit", limit), ("theme_id", theme_id)) if v}
    rows = llm_runner.rows_for(db, tache, task, scope)
    return {"rows": len(rows), "cap": llm_runner.MAX_ROWS,
            "sample": [r["text"][:90] for r in rows[:3]]}


# --------------------------------------------------------------------- import


class ImportIn(BaseModel):
    """Parsed scraper output - what questions_processing/*/tache{2,3}.jsonl holds."""

    rows: list[dict]
    dry_run: bool = True


@router.post("/import")
def import_scraped(payload: ImportIn, db: Session = Depends(get_db)) -> dict:
    """Upload parsed scraper output. See load_rows."""
    return load_rows(db, payload.rows, payload.dry_run)


def load_rows(db: Session, rows_in: list[dict], dry_run: bool = True) -> dict:
    """Load scraper rows into raw_questions, fingerprints and list_questions.

    The same three-layer collapse backfill_questions.py does, and it must stay
    the same: grouping by (tache, fingerprint) with the newest sighting
    canonical is what makes ids stable across rebuilds. The logic is imported
    rather than copied for that reason.

    Defaults to a dry run. This is the one admin action that reshapes the whole
    bank, so the numbers come first and the write is a second, deliberate call.
    """
    from questions_processing.deduplication import fingerprint, normalize

    seen: dict[tuple, list] = {}
    skipped = 0
    for r in rows_in:
        if "text" not in r or "id" not in r or r.get("tache") not in (2, 3):
            skipped += 1
            continue
        period = f"{int(r.get('year') or 0):04d}-{int(r.get('month') or 0):02d}"
        r = {**r, "period": period, "fp": fingerprint(r["text"])}
        seen.setdefault((r["tache"], r["fp"]), []).append(r)

    rows = [r for group in seen.values() for r in group]
    if not rows:
        raise HTTPException(422, f"no usable rows (skipped {skipped})")

    existing_fp = {(f.tache, f.fingerprint): f
                   for f in db.scalars(select(Fingerprint)).all()}
    existing_raw = {r.id for r in db.scalars(select(RawQuestion)).all()}
    existing_lq = {(l.tache, l.period, l.f_id)
                   for l in db.scalars(select(ListQuestion)).all()}

    new_fp = sum(1 for k in seen if k not in existing_fp)
    new_raw = sum(1 for r in rows if str(r["id"]) not in existing_raw)
    months = {(r["tache"], r["period"], r["fp"]) for r in rows}
    new_lq = sum(1 for t, p, fp in months
                 if (t, p, existing_fp.get((t, fp)).id if (t, fp) in existing_fp else None)
                 not in existing_lq)

    report = {"rows": len(rows), "skipped": skipped,
              "new_fingerprints": new_fp, "new_raw_questions": new_raw,
              "new_list_questions": new_lq, "dry_run": dry_run}
    if dry_run:
        return report

    # parents before children at every layer boundary - the ordering that
    # Postgres caught and SQLite did not
    for (tache, fp), group in seen.items():
        group.sort(key=lambda r: (r["period"], str(r["id"])))
        newest, periods = group[-1], {r["period"] for r in group}
        derived = dict(text=normalize(newest["text"]),
                       first_seen=min(periods), last_seen=max(periods))
        row = existing_fp.get((tache, fp))
        if row is None:
            row = Fingerprint(tache=tache, fingerprint=fp, total_sightings=0,
                              months_seen=0, **derived)
            db.add(row)
            existing_fp[(tache, fp)] = row
        else:
            for k, v in derived.items():
                setattr(row, k, v)
    db.flush()

    for r in rows:
        rid = str(r["id"])
        if rid in existing_raw:
            continue
        group = r.get("partie") or r.get("jour") or r.get("combinaison")
        db.add(RawQuestion(
            id=rid, f_id=existing_fp[(r["tache"], r["fp"])].id, tache=r["tache"],
            text=r["text"], period=r["period"],
            partie=int(group) if group is not None else None,
            sujet=int(r["sujet"]) if r.get("sujet") is not None else None,
            source=r.get("source", "unknown"), source_url=r.get("source_url")))
    db.flush()

    by_month: dict[tuple, list] = {}
    for r in rows:
        by_month.setdefault((r["tache"], r["period"], r["fp"]), []).append(r)
    for (tache, period, fp), members in by_month.items():
        f_id = existing_fp[(tache, fp)].id
        members.sort(key=lambda r: str(r["id"]))
        if (tache, period, f_id) in existing_lq:
            continue
        db.add(ListQuestion(tache=tache, period=period, f_id=f_id,
                            month_sightings=len(members),
                            representative_raw_id=str(members[-1]["id"])))
    db.flush()

    # counts are derived, so they are recomputed from what is now stored rather
    # than incremented - an import that overlaps an earlier one must not double
    for f in existing_fp.values():
        if f.id is None:
            continue
        agg = db.execute(
            select(func.count(), func.count(func.distinct(RawQuestion.period)),
                   func.min(RawQuestion.period), func.max(RawQuestion.period))
            .where(RawQuestion.f_id == f.id)).one()
        f.total_sightings, f.months_seen, f.first_seen, f.last_seen = agg
    db.commit()

    report["totals"] = {
        "raw_questions": db.scalar(select(func.count()).select_from(RawQuestion)),
        "fingerprints": db.scalar(select(func.count()).select_from(Fingerprint)),
        "list_questions": db.scalar(select(func.count()).select_from(ListQuestion)),
    }
    return report


# --------------------------------------------------------------------- scrape


class ScrapeIn(BaseModel):
    sources: list[str] = list(scrape_runner.SOURCES)


@router.post("/scrape", status_code=status.HTTP_201_CREATED)
def start_scrape(payload: ScrapeIn, admin: User = Depends(current_admin),
                 db: Session = Depends(get_db)) -> dict:
    """Fetch new month pages, parse them, and load the questions. One button.

    No HTML is kept: pages are parsed in memory and only the questions are
    stored, so this works on a filesystem that is wiped between restarts. The
    months already in `raw_questions` are skipped, so a routine run fetches one
    page rather than the whole archive.
    """
    unknown = [s for s in payload.sources if s not in scrape_runner.SOURCES]
    if unknown:
        raise HTTPException(422, f"unknown source(s): {unknown}")

    running = db.scalar(select(func.count()).select_from(LlmJob)
                        .where(LlmJob.status.in_(("queued", "running"))))
    if running:
        raise HTTPException(409, "a job is already running - wait for it or cancel it")

    job = LlmJob(kind="scrape", tache=0, task="scrape",
                 provider=",".join(payload.sources), model="-", chunk=0,
                 scope={"sources": payload.sources}, status="queued",
                 created_by=admin.id)
    db.add(job)
    db.commit()
    scrape_runner.start(job.id, payload.sources)
    return _job_row(job)


# --------------------------------------------------------------------- export


@router.get("/export")
def export_questions(
    tache: int = Query(2, ge=2, le=3),
    unlabelled: Optional[str] = Query(None, pattern="^(theme|abstract|core_subject)$"),
    theme_id: Optional[int] = None,
    limit: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Questions as JSONL rows, for anything still run locally.

    Shaped exactly like export_questions.py's output - `id` and `text`, plus
    `theme` when there is one - so a downloaded file feeds llm.py unchanged.
    """
    scope = {k: v for k, v in (("limit", limit or None),
                               ("theme_id", theme_id),
                               ("unlabelled", unlabelled)) if v}
    rows = llm_runner.rows_for(db, tache, unlabelled or "theme", scope)
    return [{"id": int(r["id"]), "text": r["text"],
             **({"theme": r["theme"]} if r["theme"] else {})} for r in rows]
