#!/usr/bin/env python3
"""Load llm.py's theme/abstract/core_subject output into the fingerprints table.

    backend/.venv/bin/python backend/load_labels.py out_theme.jsonl
    backend/.venv/bin/python backend/load_labels.py --dry-run out_*.jsonl

    # against Neon, same as the other backend scripts
    DATABASE_URL='postgresql+psycopg://...' backend/.venv/bin/python backend/load_labels.py ...

Input is any JSONL with `id` and one answer field - `theme`, `abstract` or
`core_subject`, whichever llm.py wrote. The column is chosen from that field
name, so the same command handles every task.

Labels live on the fingerprint, never on a list row
---------------------------------------------------
One fingerprint is one question, however many months it recurred in, so a
label written here shows up identically in all of them. The old model put
labels on a per-month row whose id moved when the question recurred, which is
how 49 questions ended up with a labelled copy and an unlabelled twin.

Ids it accepts
--------------
    fingerprint id   what export_questions.py writes - an integer
    scraper id       a 13-digit string, resolved through raw_questions

The second form is there because label files produced before the migration are
keyed on scraper ids. Both are exact; there is no text matching, because a
scraper id stays stable even when the wording is corrected.

Names become foreign keys
-------------------------
`theme` and `core_subject` are resolved against `themes` and `core_subjects`,
case-, space- and apostrophe-insensitively. A name that is not in the
vocabulary cannot be written - that is the drift guard working, not a bug. It
is reported so you can decide whether the label belongs in
llm_tasks.VOCABULARY (then re-run load_vocabulary.py and this script) or was a
mistake.

Idempotent: it UPDATEs, never inserts. Values already set are kept and counted
unless --overwrite, so a rerun cannot quietly undo hand-adjudicated labels.
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

from app.db import SessionLocal                                  # noqa: E402
from app.models import CoreSubject, Fingerprint, RawQuestion, Theme  # noqa: E402

# input field -> fingerprints column
COLUMNS = {"theme": "theme_id", "abstract": "abstract",
           "core_subject": "core_subject_id"}


def key(name: str) -> str:
    """Lookup form of a label name.

    Case-, whitespace- and apostrophe-insensitive: the vocabulary is normalised
    but an LLM emits whatever it likes, and a curly U+2019 must still find the
    straight quote the vocabulary uses.
    """
    return " ".join(name.replace("’", "'").split()).casefold()


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


def field_of(path: Path, rows: list[dict]) -> str:
    """Which label this file carries, from its answer field."""
    keys = {k for r in rows for k in r} & set(COLUMNS)
    if len(keys) != 1:
        sys.exit(f"{path}: expected exactly one of {sorted(COLUMNS)} per row, "
                 f"found {sorted(keys) or 'none'}")
    return keys.pop()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path, help="llm.py output JSONL")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change, write nothing")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace values that are already set")
    args = ap.parse_args()

    with SessionLocal() as db:
        fps = {f.id: f for f in db.query(Fingerprint).all()}
        by_scraper = {r.id: r.f_id for r in db.query(RawQuestion).all()}
        theme_id = {(t.tache, key(t.name)): t.id for t in db.query(Theme).all()}
        cs_id = {(c.theme_id, key(c.name)): c.id for c in db.query(CoreSubject).all()}

        total = collections.Counter()
        for path in args.files:
            data = read_jsonl(path)
            if not data:
                print(f"{path}: empty, skipped")
                continue
            field = field_of(path, data)
            col = COLUMNS[field]
            models = sorted({r.get("model") for r in data} - {None})

            stats = collections.Counter()
            unresolved = collections.Counter()
            changed = skipped = same = 0

            for r in data:
                raw_id, value = r["id"], r[field]

                # resolve the row
                f = fps.get(raw_id if isinstance(raw_id, int) else None)
                if f is not None:
                    stats["by fingerprint id"] += 1
                elif (f_id := by_scraper.get(str(raw_id))) is not None:
                    f = fps[f_id]
                    stats["by scraper id"] += 1
                else:
                    stats["unmatched"] += 1
                    continue
                if value is None or value == "":
                    stats["blank answer"] += 1
                    continue

                # resolve the value
                if field == "theme":
                    new = theme_id.get((f.tache, key(value)))
                    if new is None:
                        unresolved[f"theme {value!r}"] += 1
                        continue
                elif field == "core_subject":
                    if f.theme_id is None:
                        unresolved[f"core_subject {value!r} (question has no theme)"] += 1
                        continue
                    new = cs_id.get((f.theme_id, key(value)))
                    if new is None:
                        unresolved[f"core_subject {value!r}"] += 1
                        continue
                else:
                    new = value

                old = getattr(f, col)
                if old == new:
                    same += 1
                elif old is not None and not args.overwrite:
                    skipped += 1
                else:
                    setattr(f, col, new)
                    changed += 1

            print(f"{path.name}  ->  fingerprints.{col}"
                  f"{'  [' + ', '.join(models) + ']' if models else ''}")
            print(f"  {len(data):4} row(s): {stats['by fingerprint id']} by fingerprint id, "
                  f"{stats['by scraper id']} by scraper id, "
                  f"{stats['unmatched']} unmatched"
                  + (f", {stats['blank answer']} blank" if stats["blank answer"] else ""))
            print(f"  {changed:4} would change" if args.dry_run
                  else f"  {changed:4} updated", end="")
            print(f", {same} already identical"
                  f"{f', {skipped} left alone (use --overwrite)' if skipped else ''}")
            if unresolved:
                n = sum(unresolved.values())
                print(f"  ! {n} value(s) across {len(unresolved)} name(s) are not in "
                      f"the vocabulary:")
                for name, k in unresolved.most_common():
                    print(f"      {k:3}  {name}")
            total.update(changed=changed, unmatched=stats["unmatched"],
                         unresolved=sum(unresolved.values()))

        if args.dry_run:
            db.rollback()
            print("\ndry run - nothing written")
        else:
            db.commit()
            n = db.query(Fingerprint).count()
            print(f"\ncommitted {total['changed']} update(s)")
            for c in ("theme_id", "abstract", "core_subject_id"):
                k = db.query(Fingerprint).filter(
                    getattr(Fingerprint, c).isnot(None)).count()
                print(f"  fingerprints.{c:16} {k:5} / {n} filled  ({k / n:.0%})")


if __name__ == "__main__":
    main()
