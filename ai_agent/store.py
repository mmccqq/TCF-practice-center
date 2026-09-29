"""Database access and the embedding index for the labelling agent.

Three jobs, all read-only against the question bank:

  * the vocabulary - themes, and the core_subjects under a theme with their
    usage counts. Small enough to paste into a prompt: 16 themes and 131
    subjects is the whole tache-2 vocabulary, about 120 tokens.
  * the embedding backfill - one OpenAI call per batch of questions, stored
    as packed float32 in `fingerprint_embeddings`.
  * `EmbeddingIndex` - the search itself.

Why the vectors are normalised on write
---------------------------------------
cosine(a, b) = a.b / (|a| |b|). Store unit vectors and the divisions vanish,
so scoring the whole corpus is `M @ q` - one BLAS call over a (N, dim) matrix.
At 1,541 questions that is about 2 ms, which is why there is no vector
database here and no approximate search: the scan is exhaustive, so the top-k
is exact rather than an estimate.

Why the matrix is cached in the process
---------------------------------------
17 MB fetched from Neon on every find_similar call would dominate everything
else in the run, and the agent calls find_similar more than once per question
by design. The cache invalidates on row count, which is enough: vectors are
only ever inserted, never edited.

Nothing here writes to fingerprints. The agent reports; it does not label.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

PROJECT = Path(__file__).resolve().parent.parent
# the same entry point uvicorn uses (`--app-dir backend`), so `app.*` resolves
# here exactly as it does on the server
sys.path.insert(0, str(PROJECT / "backend"))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import (CoreSubject, Fingerprint, FingerprintEmbedding,  # noqa: E402
                        Theme)

DEFAULT_MODEL = "text-embedding-3-small"
DEFAULT_SOURCE = "text"      # the other option is "abstract"
DEFAULT_TACHE = 2


# --------------------------------------------------------------- vocabulary --

def themes(db: Session, tache: int = DEFAULT_TACHE) -> list[str]:
    """Every theme name for a task, alphabetical."""
    return list(db.scalars(select(Theme.name)
                           .where(Theme.tache == tache)
                           .order_by(Theme.name)))


def subjects(db: Session, theme: str, tache: int = DEFAULT_TACHE
             ) -> list[dict]:
    """Core subjects under one theme, with how many questions use each.

    The count is what separates an established label from a near-synonym a
    model invented last month, which is the same reason the admin Vocabulary
    tab shows it. Ordered by count so the established ones read first.
    """
    used = (select(Fingerprint.core_subject_id,
                   func.count().label("n"))
            .where(Fingerprint.tache == tache)
            .group_by(Fingerprint.core_subject_id)
            .subquery())
    rows = db.execute(
        select(CoreSubject.name, func.coalesce(used.c.n, 0))
        .join(Theme, CoreSubject.theme_id == Theme.id)
        .outerjoin(used, used.c.core_subject_id == CoreSubject.id)
        .where(Theme.tache == tache, Theme.name == theme)
        .order_by(func.coalesce(used.c.n, 0).desc(), CoreSubject.name)
    ).all()
    return [{"name": n, "uses": int(c)} for n, c in rows]


def sample_questions(db: Session, core_subject: str, n: int = 5,
                     tache: int = DEFAULT_TACHE) -> list[dict]:
    """Questions already filed under a subject - the boundary test.

    "Does this question belong with these?" is a sharper question than "does
    this label sound right?", and it is the one a reviewer actually asks.
    """
    rows = db.execute(
        select(Fingerprint.id, Fingerprint.text, Fingerprint.abstract)
        .join(CoreSubject, Fingerprint.core_subject_id == CoreSubject.id)
        .where(Fingerprint.tache == tache, CoreSubject.name == core_subject)
        .order_by(Fingerprint.months_seen.desc())
        .limit(n)
    ).all()
    return [{"f_id": i, "abstract": a, "text": t} for i, t, a in rows]


# ---------------------------------------------------------------- questions --

def question(db: Session, f_id: int) -> dict | None:
    row = db.execute(
        select(Fingerprint.id, Fingerprint.text, Fingerprint.abstract,
               Theme.name, CoreSubject.name)
        .outerjoin(Theme, Fingerprint.theme_id == Theme.id)
        .outerjoin(CoreSubject, Fingerprint.core_subject_id == CoreSubject.id)
        .where(Fingerprint.id == f_id)
    ).first()
    if row is None:
        return None
    return {"f_id": row[0], "text": row[1], "abstract": row[2],
            "theme": row[3], "core_subject": row[4]}


def queue(db: Session, tache: int = DEFAULT_TACHE, limit: int | None = None
          ) -> list[dict]:
    """Every question the agent can work on: it needs a theme, because
    core_subjects are scoped to one by foreign key and a subject drawn from
    the wrong theme's list is worse than no subject at all.

    Already-labelled questions are included deliberately - re-checking them is
    what produces a silver agreement rate on day one, with no hand-labelling.
    """
    q = (select(Fingerprint.id)
         .where(Fingerprint.tache == tache, Fingerprint.theme_id.is_not(None))
         .order_by(Fingerprint.id))
    if limit:
        q = q.limit(limit)
    return [question(db, i) for i in db.scalars(q)]


# --------------------------------------------------------------- embeddings --

def _pack(vec: Sequence[float]) -> tuple[bytes, int]:
    """float32 and unit length. Both matter - see the module docstring."""
    a = np.asarray(vec, dtype=np.float32)     # NOT float64: half the bytes
    n = float(np.linalg.norm(a))
    if n > 0:
        a = a / n
    return a.astype(np.float32).tobytes(), a.shape[0]


def embed_texts(texts: list[str], model: str = DEFAULT_MODEL) -> list[list[float]]:
    """One API call for a batch. Import is local so that nothing here needs
    an API key until something actually embeds."""
    from openai import OpenAI
    client = OpenAI()
    resp = client.embeddings.create(model=model, input=texts)
    # the API does not promise input order, but it does return `index`
    return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]


def pending(db: Session, model: str, source: str,
            tache: int = DEFAULT_TACHE) -> list[tuple[int, str]]:
    """(f_id, text) for questions with no vector for this (model, source).

    Resume is this function: re-running after a crash skips whatever landed.
    """
    field = Fingerprint.abstract if source == "abstract" else Fingerprint.text
    done = select(FingerprintEmbedding.f_id).where(
        FingerprintEmbedding.model == model,
        FingerprintEmbedding.source == source)
    rows = db.execute(
        select(Fingerprint.id, field)
        .where(Fingerprint.tache == tache,
               field.is_not(None), field != "",
               Fingerprint.id.not_in(done))
        .order_by(Fingerprint.id)
    ).all()
    return [(i, t) for i, t in rows]


def backfill(db: Session, model: str = DEFAULT_MODEL,
             source: str = DEFAULT_SOURCE, tache: int = DEFAULT_TACHE,
             batch: int = 128, limit: int | None = None,
             log=print) -> int:
    """Embed everything missing a vector. Commits per batch, so a run killed
    halfway leaves that many vectors behind rather than nothing."""
    todo = pending(db, model, source, tache)
    if limit:
        todo = todo[:limit]
    log(f"{len(todo)} to embed  ({model}, source={source})")
    written = 0
    for start in range(0, len(todo), batch):
        chunk = todo[start:start + batch]
        vecs = embed_texts([t for _, t in chunk], model=model)
        for (f_id, _), v in zip(chunk, vecs):
            blob, dim = _pack(v)
            db.add(FingerprintEmbedding(f_id=f_id, model=model, source=source,
                                        dim=dim, vec=blob))
        db.commit()
        written += len(chunk)
        log(f"  {written}/{len(todo)}")
    return written


# --------------------------------------------------------------- the index --

class EmbeddingIndex:
    """The corpus matrix, loaded once and reused.

    Only questions that carry a core_subject are searchable: an unlabelled
    neighbour is not evidence for a label, so returning it would spend context
    on a row the agent cannot use.
    """

    def __init__(self, model: str = DEFAULT_MODEL, source: str = DEFAULT_SOURCE,
                 tache: int = DEFAULT_TACHE):
        self.model, self.source, self.tache = model, source, tache
        self._M: np.ndarray | None = None
        self._meta: list[dict] = []
        self._count = -1

    def _rows(self, db: Session):
        return db.execute(
            select(FingerprintEmbedding.f_id, FingerprintEmbedding.vec,
                   Fingerprint.text, Fingerprint.abstract,
                   Theme.name, CoreSubject.name)
            .join(Fingerprint, Fingerprint.id == FingerprintEmbedding.f_id)
            .join(CoreSubject, Fingerprint.core_subject_id == CoreSubject.id)
            .join(Theme, Fingerprint.theme_id == Theme.id)
            .where(FingerprintEmbedding.model == self.model,
                   FingerprintEmbedding.source == self.source,
                   Fingerprint.tache == self.tache)
            .order_by(FingerprintEmbedding.f_id)
        ).all()

    def load(self, db: Session) -> None:
        n = db.scalar(select(func.count()).select_from(FingerprintEmbedding)
                      .where(FingerprintEmbedding.model == self.model,
                             FingerprintEmbedding.source == self.source))
        if self._M is not None and n == self._count:
            return                                   # cache still valid
        rows = self._rows(db)
        if not rows:
            self._M, self._meta, self._count = None, [], n
            return
        self._M = np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
        self._meta = [{"f_id": r[0], "text": r[2], "abstract": r[3],
                       "theme": r[4], "core_subject": r[5]} for r in rows]
        self._count = n

    @property
    def size(self) -> int:
        return 0 if self._M is None else self._M.shape[0]

    def search(self, db: Session, query_vec: Sequence[float], k: int = 5,
               exclude: Iterable[int] = ()) -> list[dict]:
        self.load(db)
        if self._M is None:
            return []
        q = np.asarray(query_vec, dtype=np.float32)
        nrm = float(np.linalg.norm(q))
        if nrm > 0:
            q = q / nrm
        if q.shape[0] != self._M.shape[1]:
            # mixing dimensions raises nothing and returns nonsense, so refuse
            raise ValueError(f"query dim {q.shape[0]} != index dim "
                             f"{self._M.shape[1]} - wrong embedding model?")
        scores = self._M @ q                          # the entire search
        drop = set(exclude)
        order = np.argsort(-scores)
        out = []
        for i in order:
            m = self._meta[i]
            if m["f_id"] in drop:
                continue
            out.append({**m, "score": round(float(scores[i]), 4)})
            if len(out) >= k:
                break
        return out


def session() -> Session:
    return SessionLocal()
