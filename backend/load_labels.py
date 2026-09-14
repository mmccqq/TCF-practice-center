#!/usr/bin/env python3
"""Load llm.py's theme/abstract output into the questions table.

    backend/.venv/bin/python backend/load_labels.py \\
        questions_processing/gold_set/tache2_gold_theme_gpt-5.6-luna.jsonl

    # several at once - the column is inferred per file
    backend/.venv/bin/python backend/load_labels.py questions_processing/**/*_theme_*.jsonl

    # against Neon, same as seeding
    DATABASE_URL='postgresql+psycopg://...' backend/.venv/bin/python backend/load_labels.py ...

Input is any JSONL with `id` and one answer field - `theme` or `abstract`,
whichever llm.py wrote. The column is chosen from that field name, so the same
command handles both tasks.

Idempotent: it UPDATEs, never inserts. A question with no matching row is
reported, not created - questions come from seed.py, labels only annotate them.

Why ids alone are not enough
----------------------------
seed.py and deduplication.py both collapse duplicate questions, and they pick
*different* survivors: seed.py keeps the newest scraper id, deduplication.py
keeps its own canonical. So a label keyed on one pipeline's id can be missing
from the other's table - 11 of 100 on the first real file.

Both use the same fingerprint(), though, so a labelled question that has no id
match almost always has a text-fingerprint twin in the table. That fallback is
what `--source` enables, and it recovered all 11. It is a workaround for having
two dedup pipelines; the cluster model in the design doc is the actual fix.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PROJECT))

from app.db import SessionLocal                # noqa: E402
from app.models import Question                # noqa: E402

try:
    from questions_processing.deduplication import fingerprint   # noqa: E402
except ImportError:                                              # pragma: no cover
    fingerprint = None

COLUMNS = {"theme", "abstract"}


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"not found: {path}")
    rows = []
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                sys.exit(f"{path}:{n}: invalid JSON - {exc}")
    return rows


def column_of(path: Path, rows: list[dict]) -> str:
    """Which column this file fills, from its answer field."""
    keys = {k for r in rows for k in r} & COLUMNS
    if len(keys) != 1:
        sys.exit(f"{path}: expected exactly one of {sorted(COLUMNS)} per row, "
                 f"found {sorted(keys) or 'none'}")
    return keys.pop()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path, help="llm.py output JSONL")
    ap.add_argument("--source", type=Path, action="append", default=[],
                    help="questions JSONL used to recover ids the table does not "
                         "have, by matching on text fingerprint. Repeatable")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change, write nothing")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace values that are already set (default: keep them "
                         "and report the count, so a rerun cannot quietly undo "
                         "hand-adjudicated labels)")
    args = ap.parse_args()

    # id -> text, for the fingerprint fallback
    src_text: dict[str, str] = {}
    for path in args.source:
        for r in read_jsonl(path):
            if r.get("id") and r.get("text"):
                src_text[str(r["id"])] = r["text"]

    with SessionLocal() as db:
        rows = db.query(Question.id, Question.text).all()
        by_id = {qid for qid, _ in rows}
        by_fp: dict[str, str] = {}
        if fingerprint and src_text:
            for qid, text in rows:
                by_fp.setdefault(fingerprint(text), qid)

        total = collections.Counter()
        for path in args.files:
            data = read_jsonl(path)
            if not data:
                print(f"{path}: empty, skipped")
                continue
            col = column_of(path, data)
            models = sorted({r.get("model") for r in data} - {None})

            hits: dict[str, str] = {}          # question id -> value
            stats = collections.Counter()
            for r in data:
                qid, value = str(r["id"]), r[col]
                if qid in by_id:
                    stats["by_id"] += 1
                elif (fp := fingerprint(src_text[qid])) in by_fp \
                        if fingerprint and qid in src_text else False:
                    qid = by_fp[fp]
                    stats["by_fingerprint"] += 1
                else:
                    stats["unmatched"] += 1
                    continue
                hits[qid] = value

            changed = skipped = same = 0
            for q in db.query(Question).filter(Question.id.in_(hits)).all():
                new = hits[q.id]
                old = getattr(q, col)
                if old == new:
                    same += 1
                elif old is not None and not args.overwrite:
                    skipped += 1
                else:
                    setattr(q, col, new)
                    changed += 1

            print(f"{path.name}  ->  questions.{col}"
                  f"{'  [' + ', '.join(models) + ']' if models else ''}")
            print(f"  {len(data):4} row(s): {stats['by_id']} matched by id, "
                  f"{stats['by_fingerprint']} by fingerprint, "
                  f"{stats['unmatched']} unmatched")
            print(f"  {changed:4} would change" if args.dry_run
                  else f"  {changed:4} updated", end="")
            print(f", {same} already identical"
                  f"{f', {skipped} left alone (use --overwrite)' if skipped else ''}")
            if stats["unmatched"] and not src_text:
                print("  ! pass --source <questions jsonl> to match the rest by text")
            total.update(changed=changed, unmatched=stats["unmatched"])

        if args.dry_run:
            db.rollback()
            print("\ndry run - nothing written")
        else:
            db.commit()
            filled = {c: db.query(Question).filter(getattr(Question, c).isnot(None)).count()
                      for c in sorted(COLUMNS)}
            n = db.query(Question).count()
            print(f"\ncommitted {total['changed']} update(s)")
            for c, k in filled.items():
                print(f"  questions.{c:9} {k:5} / {n} rows filled  ({k / n:.0%})")


if __name__ == "__main__":
    main()
