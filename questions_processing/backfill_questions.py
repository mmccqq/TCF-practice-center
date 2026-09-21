#!/usr/bin/env python3
"""Backfill the raw_questions, fingerprints and list_questions tables.

    backend/.venv/bin/python backend/backfill_questions.py --dry-run
    backend/.venv/bin/python backend/backfill_questions.py
    backend/.venv/bin/python backend/backfill_questions.py --tache 2

    # against Neon, same as the other backend scripts
    DATABASE_URL='postgresql+psycopg://...' backend/.venv/bin/python backend/backfill_questions.py

Steps 2 and 3 of "Core question set design.md". Reads the same files seed.py
reads and writes all three lower layers:

    raw_questions    one row per scraped sighting, unnormalised, with an f_id
    fingerprints     one row per unique text per task, plus derived counts
    list_questions   one row per (task, month, fingerprint) - the list page

Expected on the current corpus:

    raw_questions   8,128    (4,231 tache 2 + 3,897 tache 3)
    fingerprints    2,751    (1,512 + 1,239)
    list_questions  6,646    (3,463 + 3,183)

All three are built in one pass because they are three groupings of the same
input; splitting them into separate scripts would mean reading and fingerprinting
8k rows twice and risking two different answers.

Nothing reads these tables yet, so this is safe to run, re-run, and get wrong.

Idempotent: fingerprints are matched on (tache, fingerprint), raw rows on their
scraper id, and list rows on (tache, period, f_id), so a re-run after a re-scrape
updates in place and every id stays put. Label columns (theme_id, abstract,
core_subject_id) are never touched - they are filled by a separate labelling
step, and a backfill must not undo hand-adjudicated work.

Why this does not reuse seed.py's collect()
-------------------------------------------
seed.py collapses to ONE row per question and throws the members away. Here the
members are the point: raw_questions keeps every sighting, and fingerprints'
first_seen / last_seen / total_sightings / months_seen are computed from them.
Both use the same fingerprint() so the two agree on what "the same question"
means.
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
DATA_DIR = PROJECT / "questions_processing"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PROJECT))

from app.db import SessionLocal                          # noqa: E402
from app.models import Fingerprint, ListQuestion, RawQuestion   # noqa: E402

from questions_processing.deduplication import fingerprint, normalize  # noqa: E402

DEFAULT_GLOBS = ["questions_formation/tache*.jsonl",
                 "questions_opal/tache*.jsonl",
                 "questions_reussir/tache*.jsonl"]


def wanted(path: Path) -> bool:
    """Skip derived files (*_semantic.jsonl etc) - only raw scraper output."""
    return path.stem in {"tache2", "tache3"}


def period_of(row: dict) -> str:
    return f"{int(row.get('year') or 0):04d}-{int(row.get('month') or 0):02d}"


def read(paths: list[Path], only: int | None) -> list[dict]:
    """Every sighting, annotated with its period and fingerprint."""
    out, skipped = [], collections.Counter()
    for path in paths:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                if "text" not in d or "id" not in d:
                    skipped["no id or text"] += 1
                    continue
                if d.get("tache") not in (2, 3):
                    skipped["tache not 2 or 3"] += 1
                    continue
                if only and d["tache"] != only:
                    continue
                d["period"] = period_of(d)
                d["fp"] = fingerprint(d["text"])
                out.append(d)
    for reason, n in skipped.items():
        print(f"  ! skipped {n} row(s): {reason}", file=sys.stderr)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path,
                    help="JSONL files (default: all scraper output)")
    ap.add_argument("-t", "--tache", type=int, choices=(2, 3),
                    help="only this task (default: both)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change, write nothing")
    ap.add_argument("--prune", action="store_true",
                    help="delete raw and list rows the input no longer "
                         "contains. Off by default: a partial input would "
                         "otherwise look like a deletion")
    args = ap.parse_args()

    if args.files:
        paths = args.files
    else:
        paths = sorted({Path(p) for g in DEFAULT_GLOBS
                        for p in glob.glob(str(DATA_DIR / g))})
        paths = [p for p in paths if wanted(p)]
    missing = [p for p in paths if not p.exists()]
    if missing:
        sys.exit(f"not found: {missing}")
    if not paths:
        sys.exit(f"no input files under {DATA_DIR} - run the scrapers first")

    print(f"reading {len(paths)} file(s)")
    rows = read(paths, args.tache)
    if not rows:
        sys.exit("no usable rows")

    # (tache, fp) -> the sightings that share it
    groups: dict[tuple[int, str], list[dict]] = collections.defaultdict(list)
    for r in rows:
        groups[(r["tache"], r["fp"])].append(r)

    print(f"\n{len(rows):6} sighting(s) -> {len(groups):5} fingerprint(s)")
    for t in sorted({r["tache"] for r in rows}):
        n_raw = sum(1 for r in rows if r["tache"] == t)
        n_fp = sum(1 for k in groups if k[0] == t)
        n_lq = len({(r["period"], r["fp"]) for r in rows if r["tache"] == t})
        print(f"  tache {t}: {n_raw:5} raw, {n_fp:5} fingerprints, "
              f"{n_lq:5} (period, fingerprint) pairs")
    print(f"  {'':8} {'':5}       {'':5}              "
          f"{len({(r['tache'], r['period'], r['fp']) for r in rows}):5} total "
          f"list_questions")

    stats = collections.Counter()
    with SessionLocal() as db:
        # ---- layer 2: fingerprints ----
        existing_fp = {(f.tache, f.fingerprint): f
                       for f in db.query(Fingerprint).all()}
        for (tache, fp), members in groups.items():
            # same rule as seed.py: newest sighting supplies the wording
            members.sort(key=lambda r: (r["period"], str(r["id"])))
            newest = members[-1]
            periods = {r["period"] for r in members}
            derived = dict(text=normalize(newest["text"]),
                           first_seen=min(periods), last_seen=max(periods),
                           total_sightings=len(members), months_seen=len(periods))

            row = existing_fp.get((tache, fp))
            if row is None:
                # label columns left NULL on purpose - a separate step fills them
                row = Fingerprint(tache=tache, fingerprint=fp, **derived)
                db.add(row)
                existing_fp[(tache, fp)] = row
                stats["fp added"] += 1
            elif any(getattr(row, k) != v for k, v in derived.items()):
                for k, v in derived.items():
                    setattr(row, k, v)
                stats["fp updated"] += 1
            else:
                stats["fp unchanged"] += 1

        # ids for the new fingerprints, so raw rows can point at them.
        # Every layer boundary below gets its own flush: the parent rows must
        # physically exist before a child references them, and leaving that to
        # SQLAlchemy's mapper ordering is not safe. It picked raw-before-list on
        # SQLite and list-before-raw on Postgres, where the foreign key on
        # list_questions.representative_raw_id then failed the whole insert.
        db.flush()

        # ---- layer 1: raw_questions ----
        existing_raw = {r.id: r for r in db.query(RawQuestion).all()}
        seen_ids = set()
        for r in rows:
            rid = str(r["id"])
            seen_ids.add(rid)
            group = r.get("partie") or r.get("jour") or r.get("combinaison")
            fields = dict(
                f_id=existing_fp[(r["tache"], r["fp"])].id,
                tache=r["tache"],
                text=r["text"],                      # unnormalised: the evidence
                period=r["period"],
                partie=int(group) if group is not None else None,
                sujet=int(r["sujet"]) if r.get("sujet") is not None else None,
                source=r.get("source", "unknown"),
                source_url=r.get("source_url"),
            )
            row = existing_raw.get(rid)
            if row is None:
                db.add(RawQuestion(id=rid, **fields))
                stats["raw added"] += 1
            elif any(getattr(row, k) != v for k, v in fields.items()):
                for k, v in fields.items():
                    setattr(row, k, v)
                stats["raw updated"] += 1
            else:
                stats["raw unchanged"] += 1

        # raw rows must exist before list_questions can point at one as its
        # representative - see the note on the flush above
        db.flush()

        # ---- layer 3: list_questions ----
        # a third grouping of the same rows: one per (tache, period, fingerprint)
        by_month: dict[tuple[int, str, str], list[dict]] = collections.defaultdict(list)
        for r in rows:
            by_month[(r["tache"], r["period"], r["fp"])].append(r)

        existing_lq = {(lq.tache, lq.period, lq.f_id): lq
                       for lq in db.query(ListQuestion).all()}
        seen_lq = set()
        for (tache, period, fp), members in by_month.items():
            f_id = existing_fp[(tache, fp)].id
            seen_lq.add((tache, period, f_id))
            # newest sighting within the month supplies the representative
            members.sort(key=lambda r: str(r["id"]))
            fields = dict(month_sightings=len(members),
                          representative_raw_id=str(members[-1]["id"]))
            row = existing_lq.get((tache, period, f_id))
            if row is None:
                db.add(ListQuestion(tache=tache, period=period, f_id=f_id, **fields))
                stats["list added"] += 1
            elif any(getattr(row, k) != v for k, v in fields.items()):
                for k, v in fields.items():
                    setattr(row, k, v)
                stats["list updated"] += 1
            else:
                stats["list unchanged"] += 1

        stale_lq = [k for k in existing_lq if k not in seen_lq]
        if args.tache:
            stale_lq = [k for k in stale_lq if k[0] == args.tache]
        if stale_lq:
            if args.prune:
                for k in stale_lq:
                    db.delete(existing_lq[k])
                stats["list deleted"] = len(stale_lq)
            else:
                print(f"\n  ! {len(stale_lq)} list row(s) in the table are not in "
                      f"the input; pass --prune to delete them")

        # ---- rows the input no longer produces ----
        stale = [rid for rid in existing_raw if rid not in seen_ids]
        if args.tache:                     # a one-task run says nothing about the other
            stale = [rid for rid in stale if existing_raw[rid].tache == args.tache]
        if stale:
            if args.prune:
                for rid in stale:
                    db.delete(existing_raw[rid])
                stats["raw deleted"] = len(stale)
            else:
                print(f"\n  ! {len(stale)} raw row(s) in the table are not in the "
                      f"input; pass --prune to delete them")

        print()
        for k in ("fp added", "fp updated", "fp unchanged",
                  "raw added", "raw updated", "raw unchanged", "raw deleted",
                  "list added", "list updated", "list unchanged", "list deleted"):
            if stats[k]:
                print(f"  {stats[k]:6} {k}")

        if args.dry_run:
            db.rollback()
            print("\ndry run - nothing written")
            return

        db.commit()
        print(f"\ncommitted. raw_questions {db.query(RawQuestion).count()}, "
              f"fingerprints {db.query(Fingerprint).count()}, "
              f"list_questions {db.query(ListQuestion).count()}")
        for t in (2, 3):
            print(f"  tache {t}: "
                  f"{db.query(RawQuestion).filter(RawQuestion.tache == t).count():5} raw, "
                  f"{db.query(Fingerprint).filter(Fingerprint.tache == t).count():5} fp, "
                  f"{db.query(ListQuestion).filter(ListQuestion.tache == t).count():5} list")


if __name__ == "__main__":
    main()
