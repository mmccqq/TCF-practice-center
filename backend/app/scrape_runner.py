"""Scrape, parse and load new questions - the whole intake, server-side.

No HTML is stored
-----------------
The scrapers keep a disk cache of every month page (35 MB and growing) so that
a parser fix can be replayed over the archive without re-downloading it. That
is a developer's need, and it does not travel: Render's filesystem is wiped on
every restart, so a cache there would be rebuilt from nothing each run.

It is also unnecessary, because `parse_month()` is a pure function of one
page's HTML. The server fetches a page, parses it in memory, and keeps only
the questions. What months exist is already recorded in `raw_questions.period`,
so the database is the cache.

An incremental run therefore fetches the index plus whatever months are new -
usually one page, not fifty-seven.

Re-parsing the archive after a parser change is still a local job: fix the
parser, re-run the scraper on the cached HTML, and push the result through the
Data tab's import.
"""
from __future__ import annotations

import sys
import threading
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import distinct, select

from .db import SessionLocal
from .models import LlmJob, RawQuestion, utcnow

PROJECT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT / "questions_processing"))

# Months already loaded are skipped - except the most recent few, because a
# site can add questions to the current month after first publishing it, and
# skipping those would quietly lose them.
ALWAYS_REFETCH = 2

# A page every second or so, and a ceiling on how many a single run may pull.
# The scrapers are polite by default; this keeps the server the same.
DELAY = 1.0
MAX_PAGES = 12

SOURCES = ("reussir", "formation")


def _sane(slug: str, period: str) -> bool:
    """Reject entries a source's index produced badly.

    formation's hub yields the occasional slug that is a whole URL with the
    punctuation stripped, which becomes a 404 and a wasted polite second. A
    month slug is short and has no scheme in it.
    """
    return (bool(slug) and len(slug) <= 40 and "http" not in slug
            and period[:4].isdigit() and period[-2:] != "00")

_lock = threading.Lock()


def _periods_loaded(db, source: str) -> set[str]:
    return {p for (p,) in db.execute(
        select(distinct(RawQuestion.period)).where(RawQuestion.source == source)).all()}


def _reussir_plan(db) -> list[tuple[str, str, str]]:
    """(url, slug, period) for each month page worth fetching."""
    import scraper_reussir as S

    session = S.requests.Session()
    session.headers.update({"User-Agent": S.USER_AGENT,
                            "Accept-Language": "fr-FR,fr;q=0.9"})
    index = S.polite_get(session, S.INDEX_URL, DELAY)
    have = _periods_loaded(db, "reussir")

    plan = []
    for url in S.discover_month_urls(index):
        slug = S.slug_of(url)
        if not slug:
            continue
        name, year = slug.rsplit("-", 1)
        period = f"{int(year):04d}-{S.MONTHS.get(name, 0):02d}"
        plan.append((url, slug, period))
    plan.sort(key=lambda t: t[2], reverse=True)
    plan = [(u, s, p) for u, s, p in plan if _sane(s, p)]
    plan = [(u, s, p) for u, s, p in plan if _sane(s, p)]
    recent = {p for _, _, p in plan[:ALWAYS_REFETCH]}
    return [(u, s, p) for u, s, p in plan if p not in have or p in recent][:MAX_PAGES]


def _reussir_fetch(url: str, slug: str) -> list[dict]:
    import scraper_reussir as S

    session = S.requests.Session()
    session.headers.update({"User-Agent": S.USER_AGENT,
                            "Accept-Language": "fr-FR,fr;q=0.9"})
    html = S.polite_get(session, url, DELAY)
    return [asdict(q) for q in S.parse_month(html, slug, url)]


def _formation_plan(db) -> list[tuple[str, str, str]]:
    import scraper_formation as S

    sess = S.PoliteSession(delay=DELAY)
    hub = sess.get(S.HUB_URL)
    have = _periods_loaded(db, "formation")

    plan = []
    for m in S.discover_months(hub):
        period = f"{m.year:04d}-{S.month_num(m.name):02d}"
        plan.append((f"{S.HUB_URL}/{m.slug}", m.slug, period))
    plan.sort(key=lambda t: t[2], reverse=True)
    recent = {p for _, _, p in plan[:ALWAYS_REFETCH]}
    return [(u, s, p) for u, s, p in plan if p not in have or p in recent][:MAX_PAGES]


def _formation_fetch(url: str, slug: str) -> list[dict]:
    import scraper_formation as S

    sess = S.PoliteSession(delay=DELAY)
    html = sess.get(url)
    info = next((m for m in S.discover_months(sess.get(S.HUB_URL)) if m.slug == slug), None)
    if info is None:
        return []
    return [asdict(q) for q in S.parse_month(html, info, url)]


PLAN = {"reussir": _reussir_plan, "formation": _formation_plan}
FETCH = {"reussir": _reussir_fetch, "formation": _formation_fetch}


def start(job_id: int, sources: list[str]) -> None:
    threading.Thread(target=_run, args=(job_id, sources), daemon=True).start()


def _run(job_id: int, sources: list[str]) -> None:
    if not _lock.acquire(blocking=False):
        with SessionLocal() as db:
            j = db.get(LlmJob, job_id)
            j.status, j.error = "failed", "another job is already running"
            j.finished_at = utcnow()
            db.commit()
        return
    try:
        _execute(job_id, sources)
    finally:
        _lock.release()


def _execute(job_id: int, sources: list[str]) -> None:
    from .routers.admin_jobs import load_rows        # the import the Data tab uses

    rows: list[dict] = []
    notes: list[str] = []

    with SessionLocal() as db:
        job = db.get(LlmJob, job_id)
        job.status = "running"
        db.commit()

        plan: list[tuple[str, str, str, str]] = []
        for source in sources:
            try:
                for url, slug, period in PLAN[source](db):
                    plan.append((source, url, slug, period))
            except Exception as exc:                  # noqa: BLE001
                notes.append(f"{source}: could not read the index - {exc}")

        job.total_chunks = len(plan)
        job.done_chunks = 0
        db.commit()

    for n, (source, url, slug, period) in enumerate(plan, 1):
        with SessionLocal() as db:
            if db.get(LlmJob, job_id).status == "cancelled":
                return
        try:
            found = FETCH[source](url, slug)
            rows.extend(found)
            notes.append(f"{source} {period}: {len(found)} question(s)")
        except Exception as exc:                      # noqa: BLE001
            notes.append(f"{source} {period}: failed - {exc}")
        with SessionLocal() as db:
            j = db.get(LlmJob, job_id)
            j.done_chunks, j.answered = n, len(rows)
            db.commit()

    with SessionLocal() as db:
        j = db.get(LlmJob, job_id)
        if j.status == "cancelled":
            return
        if rows:
            report = load_rows(db, rows, dry_run=False)
            notes.append(
                f"loaded: {report['new_raw_questions']} new sightings, "
                f"{report['new_fingerprints']} new questions, "
                f"{report['new_list_questions']} new monthly entries")
        else:
            notes.append("nothing new to load")
        j.status = "done"
        j.error = "\n".join(notes)[:4000]     # the report, not an error
        j.finished_at = utcnow()
        db.commit()
