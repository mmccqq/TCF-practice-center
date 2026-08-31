#!/usr/bin/env python3
"""Load the scrapers' JSONL output into the questions table.

    backend/.venv/bin/python backend/seed.py                  # all sources
    backend/.venv/bin/python backend/seed.py --reset          # wipe first
    backend/.venv/bin/python backend/seed.py questions_reussir/tache2.jsonl

Idempotent: rows are upserted on the scraper id, so re-running after a
re-scrape updates in place instead of duplicating.

Note on `occurrences`: raw scraper files carry one row per sighting, so the
same question text appears many times with different ids. Those rows are
collapsed here on normalised text (the same tier-1 rule deduplication.py
uses), keeping the most recent id as the canonical one and counting the rest
- which is what makes the phase 2 high-frequency banks possible.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))          # to import deduplication.py

from app.db import Base, SessionLocal, engine   # noqa: E402
from app.models import Question                 # noqa: E402

try:
    from deduplication import fingerprint, normalize   # noqa: E402
except ImportError:                                     # pragma: no cover
    print("! deduplication.py not importable - falling back to raw text keys",
          file=sys.stderr)
    normalize = lambda s: " ".join(s.split())           # noqa: E731
    fingerprint = normalize                             # noqa: E731

DEFAULT_GLOBS = ["questions_formation/tache*.jsonl",
                 "questions_opal/tache*.jsonl",
                 "questions_reussir/tache*.jsonl"]


def wanted(path: Path) -> bool:
    """Skip derived files (*_semantic.jsonl etc) - only raw scraper output."""
    return path.stem in {"tache2", "tache3"}


def collect(paths: list[Path]) -> list[dict]:
    groups: dict = defaultdict(list)
    for path in paths:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                if "text" not in d or "id" not in d:
                    continue
                # group per tache so a tache2 and tache3 question with the same
                # wording never collapse into one another
                groups[(d.get("tache"), fingerprint(d["text"]))].append(d)

    out = []
    for (tache, _fp), rows in groups.items():
        def period(r: dict) -> str:
            return f"{int(r.get('year') or 0):04d}-{int(r.get('month') or 0):02d}"
        rows.sort(key=lambda r: (period(r), str(r["id"])))
        head = rows[-1]                       # newest sighting is canonical
        group = head.get("partie") or head.get("jour") or head.get("combinaison")
        out.append({
            "id": str(head["id"]),
            "tache": int(tache),
            "text": normalize(head["text"]),
            "source": head.get("source", "unknown"),
            "year": int(head.get("year") or 0),
            "month": int(head.get("month") or 0),
            "period": period(head),
            "partie": int(group) if group is not None else None,
            "sujet": int(head["sujet"]) if head.get("sujet") is not None else None,
            "source_url": head.get("source_url"),
            "occurrences": len(rows),
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="JSONL files (default: all scraper output)")
    ap.add_argument("--reset", action="store_true", help="delete existing questions first")
    args = ap.parse_args()

    project = ROOT.parent
    if args.files:
        paths = [Path(f) for f in args.files]
    else:
        paths = sorted({Path(p) for g in DEFAULT_GLOBS
                        for p in glob.glob(str(project / g))})
        paths = [p for p in paths if wanted(p)]
    missing = [p for p in paths if not p.exists()]
    if missing:
        sys.exit(f"not found: {missing}")
    if not paths:
        sys.exit("no input files found - run the scrapers' `parse` step first")

    print(f"reading {len(paths)} file(s):")
    for p in paths:
        print("   ", p.relative_to(project) if p.is_absolute() else p)

    records = collect(paths)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if args.reset:
            n = db.query(Question).delete()
            print(f"deleted {n} existing question(s)")
        existing = {q.id for q in db.query(Question.id).all()}
        added = updated = 0
        for rec in records:
            if rec["id"] in existing:
                db.query(Question).filter(Question.id == rec["id"]).update(rec)
                updated += 1
            else:
                db.add(Question(**rec))
                added += 1
        db.commit()
        total = db.query(Question).count()
        t2 = db.query(Question).filter(Question.tache == 2).count()
        t3 = db.query(Question).filter(Question.tache == 3).count()

    print(f"\ncollapsed to {len(records)} unique question(s)")
    print(f"  added   {added}")
    print(f"  updated {updated}")
    print(f"\nquestions table: {total} rows  (tache2={t2}, tache3={t3})")


if __name__ == "__main__":
    main()
