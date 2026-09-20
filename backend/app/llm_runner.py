"""Run a labelling job in the background, and turn its answers into a review batch.

The admin system used to only ingest llm.py's output. This runs it.

What is reused and what is not
------------------------------
Everything about talking to a model is llm.py's: the provider classes, the
prompt assembly in llm_tasks, chunking, and `parse_chunk`'s alignment check.
Reimplementing any of that would give two versions of "what did the model
say", which is exactly the split this project already paid for once.

What is different: output goes to a review batch instead of a JSONL file, and
progress goes to a database row instead of stdout.

Why a thread and not a worker
-----------------------------
Render's free tier has one process and no worker service, so the job runs in a
daemon thread inside the web process. That is fine for a few API calls with
long waits, and wrong for anything CPU-bound.

It also means a job dies when the process does - a deploy, or the free
instance going idle. Nothing is resumable mid-chunk, so:

  * the review batch is created BEFORE the first call, and each chunk's
    answers are written to it as they arrive. A job that dies at chunk 5 of 8
    leaves five chunks' worth of reviewable answers.
  * `reap_stale_jobs()` runs at startup and marks anything still `running` as
    `interrupted`, so a dead job cannot sit there looking alive.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

from sqlalchemy import select, update

from .db import SessionLocal
from .models import (CoreSubject, Fingerprint, LlmJob, ReviewBatch, ReviewItem,
                     Theme, utcnow)

# llm.py and llm_tasks.py live outside the backend package
PROJECT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT / "questions_processing"))

# A ceiling on one job, so a mistyped filter cannot spend an afternoon's budget
# while nobody is watching. Raise it deliberately, not by accident.
MAX_ROWS = 600

_lock = threading.Lock()


def provider_for(name: str, api_key: str | None):
    """A provider instance for one job, with the key the admin supplied.

    A copy, never the module-level singleton: those cache their HTTP client on
    first use, so injecting a key into one would leak it into every later job
    on the same process. The copy is thrown away when the job ends.

    The key is used and forgotten - it is not written to the job row, not
    logged, and not returned by any endpoint. Falling back to the environment
    keeps a server that already has keys configured working unchanged.
    """
    import copy
    import os

    from llm import PROVIDERS

    prov = copy.copy(PROVIDERS[name])
    prov._client = None
    if not api_key:
        return prov

    try:
        if hasattr(prov, "base_url"):                  # OpenAI-compatible
            import openai
            prov._client = openai.OpenAI(api_key=api_key, base_url=prov.base_url)
        else:                                          # Anthropic
            import anthropic
            prov._client = anthropic.Anthropic(api_key=api_key)
    except ModuleNotFoundError as exc:
        # the SDK is a server dependency, so say which one rather than letting
        # an import error surface as a failed chunk
        raise RuntimeError(
            f"{name} needs its SDK on the server: {prov.install_hint or exc}") from None
    return prov


def available() -> dict:
    """Which providers and tasks this deployment can actually run.

    Reported rather than assumed: the admin UI should not offer a provider
    whose key is missing on this server, and the answer differs between a
    laptop and Render.
    """
    import os

    from llm import PROVIDERS
    from llm_tasks import TASKS

    out = []
    for name, prov in PROVIDERS.items():
        names = prov.key_env if isinstance(prov.key_env, tuple) else (prov.key_env,)
        out.append({
            "name": name,
            "default_model": prov.default_model,
            "configured": any(os.environ.get(n) for n in names if n),
            "key_env": [n for n in names if n],
        })
    return {"providers": out, "tasks": sorted(TASKS), "max_rows": MAX_ROWS}


def reap_stale_jobs() -> int:
    """Mark jobs left `running` by a dead process. Called at startup."""
    with SessionLocal() as db:
        n = db.execute(
            update(LlmJob)
            .where(LlmJob.status.in_(("running", "queued")))
            .values(status="interrupted", finished_at=utcnow(),
                    error="the server restarted while this job was running")
        ).rowcount
        db.commit()
    return n


def rows_for(db, tache: int, task_name: str, scope: dict) -> list[dict]:
    """The questions a job will label, as llm.py's Task.body() expects them."""
    q = (select(Fingerprint, Theme.name)
         .outerjoin(Theme, Theme.id == Fingerprint.theme_id)
         .where(Fingerprint.tache == tache))

    # an explicit selection wins over every filter: the admin ticked those rows
    # and meant them, including ones that already carry a label
    if scope.get("f_ids"):
        # the ceiling applies here too: a selection is a filter by another name,
        # and MAX_ROWS exists to bound what one job can spend
        chosen = list(scope["f_ids"])[:MAX_ROWS]
        rows = db.execute(q.where(Fingerprint.id.in_(chosen))).all()
        return [{"id": str(f.id), "text": f.text, "theme": theme}
                for f, theme in rows
                if task_name != "core_subject" or f.theme_id is not None]

    # "unlabelled" defaults to the task's own field: labelling what is already
    # labelled is the expensive way to change nothing
    missing = scope.get("unlabelled", task_name)
    if missing == "theme":
        q = q.where(Fingerprint.theme_id.is_(None))
    elif missing == "core_subject":
        q = q.where(Fingerprint.core_subject_id.is_(None))
    elif missing == "abstract":
        q = q.where(Fingerprint.abstract.is_(None))
    if scope.get("theme_id"):
        q = q.where(Fingerprint.theme_id == scope["theme_id"])

    if task_name == "core_subject":
        # the prompt is built per theme, so a question without one cannot be
        # asked at all - llm_tasks raises rather than guessing from another
        # theme's vocabulary
        q = q.where(Fingerprint.theme_id.isnot(None))

    q = q.order_by(Fingerprint.months_seen.desc(), Fingerprint.id.desc())
    limit = min(int(scope.get("limit") or MAX_ROWS), MAX_ROWS)
    rows = db.execute(q.limit(limit)).all()
    return [{"id": str(f.id), "text": f.text, "theme": theme} for f, theme in rows]


def start(job_id: int, runs: list[dict] | None = None) -> None:
    """Run the job on a daemon thread. Returns immediately.

    `runs` is one dict per model to ask - {provider, model, api_key}. One for a
    review job, two for a compare. The keys travel as arguments rather than
    through the database: they are needed for the length of the run and nowhere
    else.
    """
    threading.Thread(target=_run, args=(job_id, runs or []), daemon=True).start()


def _run(job_id: int, runs: list[dict] | None = None) -> None:
    # one at a time: several concurrent runs would multiply the spend and hit
    # rate limits that the retry in llm.py's send() is not meant to absorb
    if not _lock.acquire(blocking=False):
        with SessionLocal() as db:
            job = db.get(LlmJob, job_id)
            job.status, job.error = "failed", "another job is already running"
            job.finished_at = utcnow()
            db.commit()
        return
    try:
        _execute(job_id, runs or [])
    finally:
        _lock.release()


def _execute(job_id: int, runs: list[dict]) -> None:
    """Ask each model in turn, merging every answer into one review batch.

    One batch, not one per model, because that is what lets a comparison be
    applied in a single pass: the agreed answers and the adjudicated ones are
    the same rows. Loading one model's output first and the disagreements
    afterwards - two passes over the same questions - is the step this removes.
    """
    from llm import chunked, parse_chunk
    from llm_tasks import TASKS

    with SessionLocal() as db:
        job = db.get(LlmJob, job_id)
        task = TASKS[job.task]
        rows = rows_for(db, job.tache, job.task, job.scope or {})
        if not rows:
            job.status, job.error = "done", "no questions matched"
            job.finished_at = utcnow()
            db.commit()
            return

        chunks = chunked(rows, job.chunk, group_by=task.group_by)
        job.total_rows = len(rows)
        job.total_chunks = len(chunks) * max(1, len(runs))
        job.status = "running"

        # created before the first call, so a job that dies partway still
        # leaves everything it did get in a reviewable place
        label = " vs ".join(r["model"] for r in runs) or job.model
        batch = ReviewBatch(name=f"{job.task} · {label} · {len(rows)} questions",
                            field=job.task, tache=job.tache,
                            created_by=job.created_by)
        db.add(batch)
        db.flush()
        job.batch_id = batch.id
        db.commit()
        batch_id = batch.id
        field = task.output_field or task.answer_key

    answered = done = 0
    notes: list[str] = []

    for run in runs:
        try:
            provider = provider_for(run["provider"], run.get("api_key"))
        except RuntimeError as exc:
            notes.append(str(exc))
            continue

        for n, chunk in enumerate(chunks, 1):
            with SessionLocal() as db:
                if db.get(LlmJob, job_id).status == "cancelled":
                    return
            try:
                reply = provider.send(chunk, run["model"], task)
                # send() reports an API failure on the Reply rather than
                # raising - an auth error or a rate limit arrives that way
                failure = reply.error
                records = [] if failure else parse_chunk(
                    reply, [r["id"] for r in chunk],
                    f"{run['model']} chunk {n}/{len(chunks)}", task)
                if not failure and not records:
                    failure = "the reply could not be used - see the server log"
            except Exception as exc:                   # noqa: BLE001
                failure, records = str(exc), []

            done += 1
            if failure:
                notes.append(f"{run['model']} chunk {n}: {failure}")
            else:
                answered += _store(batch_id, records, field, run["model"])

            with SessionLocal() as db:
                j = db.get(LlmJob, job_id)
                j.done_chunks, j.answered = done, answered
                if notes:
                    j.error = "\n".join(notes[-6:])[:2000]
                db.commit()

    with SessionLocal() as db:
        j = db.get(LlmJob, job_id)
        if j.status != "cancelled":
            j.status = "done" if answered else "failed"
        j.finished_at = utcnow()
        db.commit()


def _store(batch_id: int, records: list[dict], field: str, model: str) -> int:
    """Write one model's answers into the batch, merging onto existing items.

    The second model's pass finds items the first created, so its answer is
    added alongside rather than replacing - that merge is what makes an item
    show two candidates and a disagreement visible.
    """
    written = 0
    with SessionLocal() as db:
        ids = [int(r["id"]) for r in records if str(r["id"]).isdigit()]
        texts = {str(f.id): f.text for f in db.scalars(
            select(Fingerprint).where(Fingerprint.id.in_(ids))).all()}
        existing = {i.f_id: i for i in db.scalars(
            select(ReviewItem).where(ReviewItem.batch_id == batch_id,
                                     ReviewItem.f_id.in_(ids))).all()}
        for rec in records:
            value = rec.get(field)
            if value is None:
                continue
            f_id = int(rec["id"])
            item = existing.get(f_id)
            if item is None:
                db.add(ReviewItem(batch_id=batch_id, f_id=f_id,
                                  source_id=str(f_id), text=texts.get(str(f_id), ""),
                                  candidates={model: value}))
            else:
                # a JSON column needs a new object to register as changed
                item.candidates = {**item.candidates, model: value}
            written += 1
        db.commit()
    return written
