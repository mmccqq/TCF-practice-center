"""
Three-tier deduplication for a TCF Canada question bank (~2-4k items).

Input : JSONL as emitted by scraper_formation / scraper_opal / scraper_reussir,
        i.e. one object per line with at least {"id", "text"} and usually
        {"tache", "source", "month_slug"}.
Output: JSONL of canonical questions, each carrying the ids it absorbed.

Output goes next to the input by default, tagged with the workflow that
produced it so the two never overwrite each other:

    questions_reussir/tache2.jsonl
      --> questions_reussir/tache2_semantic.jsonl           + ..._semantic_review.jsonl
      --> questions_reussir/tache2_no_semantic.jsonl        + ..._no_semantic_review.jsonl

For a multi-file run the results span sources, so they land in the current
directory as e.g. tache2_all_semantic.jsonl. -o / -r override either.

Tache 2 and Tache 3 are meant to be deduplicated separately - run this once
per tache file. A `task` guard still refuses cross-tache merges if you ever
do concatenate them, but the intended workflow is one file at a time:

    # one source at a time
    python3 deduplication.py questions_reussir/tache2.jsonl

    # then all three sources together (ids are globally unique per tache)
    python3 deduplication.py questions_*/tache2.jsonl

    # tiers 0-1 only, no model download
    python3 deduplication.py questions_reussir/tache2.jsonl --no-semantic

Install (only needed without --no-semantic):
    pip install sentence-transformers numpy
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:                     # numpy is only needed by the semantic
    import numpy as np                # tier, so it is imported lazily below -
                                      # that keeps normalize()/fingerprint()
                                      # importable (e.g. by backend/seed.py)
                                      # without pulling numpy into that venv.

# --------------------------------------------------------------------------
# Tuning knobs. Calibrate these on a hand-labelled sample before trusting them.
# --------------------------------------------------------------------------
# Calibrated on 264 exact-deduped reussir Tache 2 questions with e5-large.
# Because every prompt shares the same template, that corpus's 34,716 pairwise
# cosines all fell in [0.81, 1.00] (p50 = 0.882, p99 = 0.936) - so the original
# 0.93 / 0.82 pair merged unrelated questions and flagged 100% of pairs for
# review. At 0.95 the merges spot-check as genuine rewordings.
AUTO_MERGE = 0.95   # >= this cosine  -> merged without review ("duplicate")
REVIEW_LOW = 0.90   # [LOW, MERGE)    -> flagged as near-duplicate, NOT merged
MAX_CLUSTER = 6     # belt-and-braces cap; complete linkage is the real guard
EMBED_MODEL = "intfloat/multilingual-e5-large"


# --------------------------------------------------------------------------
# Tier 0: normalisation
# --------------------------------------------------------------------------
APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'", "`": "'"})
QUOTES = str.maketrans({"«": '"', "»": '"', "“": '"', "”": '"'})


def normalize(text: str) -> str:
    """Collapse formatting noise that should never count as a difference."""
    t = unicodedata.normalize("NFC", text)
    t = t.translate(APOSTROPHES).translate(QUOTES)
    t = t.replace("…", "...").replace("–", "-").replace("—", "-")
    t = re.sub(r"\s+", " ", t)              # incl. French thin space before ? ! :
    t = re.sub(r"\s+([?!:;])", r"\1", t)
    t = re.sub(r"^\s*(question|sujet)\s*\d*\s*[:.\-]\s*", "", t, flags=re.I)
    return t.strip().strip("\"'").strip()


def fingerprint(text: str) -> str:
    """Aggressive key for exact-duplicate detection only (accents dropped)."""
    t = normalize(text).lower()
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9 ]", "", t)
    return hashlib.sha256(t.encode()).hexdigest()


# --------------------------------------------------------------------------
# Union-Find: turns pairwise edges into clusters
# --------------------------------------------------------------------------
class UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.size = [1] * n
        # root -> its members, kept current so complete-linkage can test every
        # cross-pair between two clusters before agreeing to merge them
        self.members: dict[int, list[int]] = {i: [i] for i in range(n)}

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]  # path compression
            x = self.parent[x]
        return x

    def union(self, a: int, b: int, max_size: int = 10**9) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.size[ra] + self.size[rb] > max_size:
            return False                                   # refuse to chain
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]
        self.members[ra].extend(self.members.pop(rb))
        return True

    def groups(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = defaultdict(list)
        for i in range(len(self.parent)):
            out[self.find(i)].append(i)
        return out


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------
@dataclass
class Question:
    id: str
    text: str
    task: str = "unknown"
    source: str = "unknown"
    month: str = ""


@dataclass
class Canonical:
    id: str                     # id of the representative wording
    text: str
    task: str
    month: str = ""             # month of the kept record; == max(months) when
                                # --canonical latest (the default)
    sources: list[str] = field(default_factory=list)
    months: list[str] = field(default_factory=list)
    occurrences: int = 1        # == 1 + len(duplicate_ids)
    # Tier 1+2, confident: same question, merged into this record.
    duplicate_ids: list[str] = field(default_factory=list)
    # Subset of duplicate_ids that are byte-identical to `text` after
    # normalisation, i.e. found by tier 1 alone. duplicate_ids minus this is
    # what the embedding model merged, so that difference is what to audit.
    exact_duplicate_ids: list[str] = field(default_factory=list)
    # Tier 2, uncertain: scored in [REVIEW_LOW, AUTO_MERGE) against this
    # record but NOT merged. These are ids of *other canonical records* -
    # review them by hand, then re-run with a lower --auto-merge if they
    # turn out to be genuine duplicates.
    near_duplicate_ids: list[str] = field(default_factory=list)
    variants: list[str] = field(default_factory=list)


def load(paths: list[str]) -> list[Question]:
    """Read one or more JSONL files produced by this project's scrapers.

    Field mapping is deliberate: the scrapers emit `tache` (an int), not
    `task`. For the month, they emit `year`+`month` ints plus a `month_slug`
    whose format differs per source ("aout-2026" for formation/reussir but
    "2024-04" for opal), so year+month is preferred and rendered as a
    uniform sortable "YYYY-MM"; month_slug is only a fallback.
    """
    rows: list[Question] = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                task = d.get("task", d.get("tache", "unknown"))
                year, mon = d.get("year"), d.get("month")
                if isinstance(year, int) and isinstance(mon, int) and mon:
                    month = f"{year:04d}-{mon:02d}"
                else:
                    month = d.get("month_slug") or d.get("month") or ""
                rows.append(
                    Question(
                        id=str(d.get("id") or f"{path}:{i}"),
                        text=d["text"],
                        task=str(task),
                        source=d.get("source", "unknown"),
                        month=str(month),
                    )
                )
    return rows


# --------------------------------------------------------------------------
# Tier 1 -> Tier 3
# --------------------------------------------------------------------------
def exact_pass(rows: list[Question]) -> list[list[Question]]:
    """Group byte-identical-after-normalisation questions.

    Returns a list of buckets rather than the fingerprint->bucket dict: the
    rest of the pipeline addresses buckets by index. Keying anything by the
    representative's *id* would be fragile - scraper ids are unique today
    (they carry a tache digit), but a bucket index is unique by construction
    and so can't silently drop a bucket if that ever regresses.
    """
    buckets: dict[str, list[Question]] = defaultdict(list)
    for q in rows:
        buckets[fingerprint(q.text)].append(q)
    return list(buckets.values())


def embed(texts: list[str], model_name: str) -> "np.ndarray":
    import numpy as np

    try:
        from sentence_transformers import SentenceTransformer
    except ModuleNotFoundError:
        raise SystemExit(
            "sentence-transformers is not installed, so the semantic tier "
            "cannot run.\n"
            "  - to skip it and keep exact dedup only:  add --no-semantic\n"
            "  - to enable it:  pip install sentence-transformers\n"
            f"    (then optionally a smaller model than the {model_name} "
            "default:\n"
            "     -m paraphrase-multilingual-MiniLM-L12-v2)"
        ) from None

    model = SentenceTransformer(model_name)
    # e5-family models expect a task prefix; harmless for most others.
    prefixed = [f"query: {t}" for t in texts] if "e5" in model_name else texts
    vecs = model.encode(prefixed, batch_size=64, show_progress_bar=True,
                        normalize_embeddings=True)
    return np.asarray(vecs, dtype=np.float32)


def semantic_pass(reps: list[Question], vecs: "np.ndarray",
                  auto_merge: float, review_low: float, max_cluster: int,
                  linkage: str = "complete"):
    """Brute-force cosine. 2k x 2k is ~4M floats: nothing at this scale.

    Linkage matters a great deal on this corpus, which is why it defaults to
    "complete" rather than the usual single-linkage union-find:

    Every TCF prompt shares a rigid template ("Je suis un(e) X... Vous me
    posez des questions... (a, b, c, etc.)"), so a sentence embedding scores
    the shared *structure* far more than the distinguishing *topic*. Measured
    on 264 real reussir Tache 2 questions with e5-large, all 34,716 pairs
    landed between 0.81 and 1.00 - so *no* pair is ever "clearly unrelated",
    and a handful of unrelated ones still creep over any given threshold.

    With single linkage one such stray edge is enough to fuse two unrelated
    clusters, because A-B and B-C merge A with C without ever comparing them.
    A real observed case: a "emission de television" question sat at 0.9548
    to its own genuine reword (correct), which sat at 0.9351 to an unrelated
    "plateforme de sorties en groupe" question (a false positive barely over
    the 0.93 line) - chaining then merged the first with the last, whose true
    similarity was only 0.9130.

    Complete linkage instead requires *every* cross-pair between the two
    clusters to clear auto_merge, so one weak member vetoes the merge. Pairs
    rejected that way are pushed to the review queue rather than dropped.
    """
    import numpy as np

    sim = vecs @ vecs.T
    np.fill_diagonal(sim, 0.0)

    uf = UnionFind(len(reps))
    review: list[tuple[int, int, float]] = []

    # Highest-confidence edges first so clusters form around real cores.
    iu = np.triu_indices(len(reps), k=1)
    order = np.argsort(-sim[iu])
    for k in order:
        i, j = int(iu[0][k]), int(iu[1][k])
        score = float(sim[i, j])
        if score < review_low:
            break
        if reps[i].task != reps[j].task:
            continue                        # never merge across task types
        if score < auto_merge:
            review.append((i, j, score))
            continue
        if linkage == "complete":
            ci, cj = uf.members[uf.find(i)], uf.members[uf.find(j)]
            weakest = float(sim[np.ix_(ci, cj)].min())
            if weakest < auto_merge:
                # this pair alone is strong enough, but merging their whole
                # clusters would put two questions together that are not
                review.append((i, j, score))
                continue
        uf.union(i, j, max_cluster)
    return uf, review


def _latest_key(q: Question):
    """Newest month wins. `month` is "YYYY-MM" (see load), so a plain string
    compare is chronological, and a missing month ("") always loses. Ties
    inside one month fall back to the well-formed/longest wording."""
    return (q.month, q.text.strip().endswith(("?", ".")), len(q.text))


def _longest_key(q: Question):
    """Longest well-formed wording usually preserves the most context."""
    return (q.text.strip().endswith(("?", ".")), len(q.text))


CANONICAL_KEYS = {"latest": _latest_key, "longest": _longest_key}


def pick_canonical(members: list[Question], strategy: str = "latest") -> Question:
    """Choose the record whose wording represents a group of duplicates."""
    return max(members, key=CANONICAL_KEYS[strategy])


def build_canonicals(buckets: list[list[Question]],
                     clusters: dict[int, list[int]],
                     review: list[tuple[int, int, float]],
                     strategy: str = "latest") -> list[Canonical]:
    """Fold clusters of buckets into Canonical records, then attach the
    uncertain (review-band) neighbours as near_duplicate_ids."""
    root_of: dict[int, int] = {}
    for root, idxs in clusters.items():
        for i in idxs:
            root_of[i] = root

    by_root: dict[int, Canonical] = {}
    for root, idxs in clusters.items():
        group: list[Question] = []
        for i in idxs:
            group.extend(buckets[i])
        head = pick_canonical(group, strategy)
        head_text = normalize(head.text)
        # "Exact" means byte-identical-after-normalisation *to the canonical*,
        # which is precisely the rest of the canonical's own fingerprint
        # bucket. Members of the cluster's other buckets got there via the
        # semantic tier, so they are not exact duplicates of anything here.
        # (Do not shortcut this as buckets[i][1:] - the canonical is chosen by
        # month/length, so it is usually not its bucket's first element, and
        # that element would then be dropped from this list entirely.)
        head_bucket = next(b for b in (buckets[i] for i in idxs)
                           if any(q.id == head.id for q in b))
        by_root[root] = Canonical(
            id=head.id,
            text=head_text,
            task=head.task,
            month=head.month,
            sources=sorted({q.source for q in group}),
            months=sorted({q.month for q in group if q.month}),
            occurrences=len(group),
            duplicate_ids=sorted({q.id for q in group} - {head.id}),
            exact_duplicate_ids=sorted({q.id for q in head_bucket} - {head.id}),
            variants=sorted({normalize(q.text) for q in group} - {head_text}),
        )

    near: dict[int, set[str]] = defaultdict(set)
    for i, j, _score in review:
        ri, rj = root_of[i], root_of[j]
        if ri == rj:
            continue                # ended up merged anyway via a stronger edge
        near[ri].add(by_root[rj].id)
        near[rj].add(by_root[ri].id)
    for root, ids in near.items():
        by_root[root].near_duplicate_ids = sorted(ids)

    return list(by_root.values())


def default_out_paths(inputs: list[Path], no_semantic: bool) -> tuple[Path, Path]:
    """Where to write when -o/-r are not given: beside the input, tagged with
    the workflow so a semantic and a no-semantic run never clobber each other.

    A multi-file run mixes sources, so writing into any one source's folder
    would be misleading - those land in the current directory instead.
    """
    tag = "no_semantic" if no_semantic else "semantic"
    if len(inputs) == 1:
        out_dir, stem = inputs[0].parent, inputs[0].stem
    else:
        stems = {p.stem for p in inputs}
        stem = f"{stems.pop()}_all" if len(stems) == 1 else "combined"
        out_dir = Path(".")
    return (out_dir / f"{stem}_{tag}.jsonl",
            out_dir / f"{stem}_{tag}_review.jsonl")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="+", type=Path,
                    help="one or more JSONL files (run one tache at a time)")
    ap.add_argument("-o", "--output", type=Path,
                    help="default: <input dir>/<stem>_{semantic|no_semantic}.jsonl")
    ap.add_argument("-r", "--review", type=Path,
                    help="default: the same, with _review before the extension")
    ap.add_argument("-m", "--model", default=EMBED_MODEL)
    ap.add_argument("--no-semantic", action="store_true",
                    help="run tiers 0-1 only (no model download)")
    ap.add_argument("--canonical", choices=tuple(CANONICAL_KEYS), default="latest",
                    help="which wording to keep for a group of duplicates: "
                         "'latest' = most recent month (default), "
                         "'longest' = longest well-formed wording")
    ap.add_argument("--linkage", choices=("complete", "single"), default="complete",
                    help="'complete' (default) needs every pair in a cluster to "
                         "clear --auto-merge; 'single' is the old union-find "
                         "behaviour and chains unrelated questions together")
    ap.add_argument("--auto-merge", type=float, default=AUTO_MERGE)
    ap.add_argument("--review-low", type=float, default=REVIEW_LOW)
    ap.add_argument("--max-cluster", type=int, default=MAX_CLUSTER)
    args = ap.parse_args()

    default_out, default_review = default_out_paths(args.input, args.no_semantic)
    out_path = args.output or default_out
    review_path = args.review or default_review
    if out_path in set(args.input) or review_path in set(args.input):
        sys.exit(f"refusing to overwrite an input file: {out_path} / {review_path}")

    rows = load(args.input)
    print(f"loaded {len(rows)} questions from {len(args.input)} file(s)")
    tasks = sorted({q.task for q in rows})
    if len(tasks) > 1:
        print(f"  ! input mixes task values {tasks} - cross-task merges are "
              f"blocked, but you probably meant to run one tache at a time")

    buckets = exact_pass(rows)
    reps = [pick_canonical(b, args.canonical) for b in buckets]
    print(f"after exact dedup: {len(buckets)} "
          f"({len(rows) - len(buckets)} exact duplicates absorbed)")

    if args.no_semantic:
        clusters = {i: [i] for i in range(len(buckets))}
        review: list[tuple[int, int, float]] = []
    else:
        vecs = embed([normalize(q.text) for q in reps], args.model)
        uf, review = semantic_pass(reps, vecs, args.auto_merge,
                                   args.review_low, args.max_cluster,
                                   args.linkage)
        clusters = uf.groups()
        print(f"after semantic merge: {len(clusters)} "
              f"({len(buckets) - len(clusters)} near-identical groups merged)")

    out = build_canonicals(buckets, clusters, review, args.canonical)

    # Cross-source repetition is the strongest available signal that a
    # question reflects something candidates actually reported seeing.
    out.sort(key=lambda c: (len(c.sources), c.occurrences), reverse=True)

    with open(out_path, "w", encoding="utf-8") as fh:
        for c in out:
            fh.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")

    with open(review_path, "w", encoding="utf-8") as fh:
        for i, j, s in sorted(review, key=lambda t: -t[2]):
            fh.write(json.dumps({"score": round(s, 4),
                                 "a": reps[i].text,
                                 "b": reps[j].text,
                                 "a_id": reps[i].id,
                                 "b_id": reps[j].id},
                                ensure_ascii=False) + "\n")

    dupes = sum(len(c.duplicate_ids) for c in out)
    with_dupes = sum(1 for c in out if c.duplicate_ids)
    flagged = sum(1 for c in out if c.near_duplicate_ids)
    assert dupes + len(out) == len(rows), "duplicate accounting lost rows"
    assert all(set(c.exact_duplicate_ids) <= set(c.duplicate_ids) for c in out), \
        "exact_duplicate_ids must be a subset of duplicate_ids"
    if args.no_semantic:
        # with no semantic tier every cluster is a single exact bucket, so the
        # two lists must coincide - a cheap check that neither drifts
        assert all(set(c.exact_duplicate_ids) == set(c.duplicate_ids) for c in out), \
            "without the semantic tier all duplicates are exact duplicates"
    print(f"\ncanonical questions : {len(out)}")
    print(f"duplicates absorbed : {dupes} across {with_dupes} canonical(s)")
    print(f"flagged near-dupes  : {flagged} canonical(s), {len(review)} pair(s)"
          f" -> {review_path}")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
