#!/usr/bin/env python3
"""Pull questions out of the database as JSONL, for the labelling tools.

    backend/.venv/bin/python backend/export_questions.py            # latest 200
    backend/.venv/bin/python backend/export_questions.py -n 50 --tache 2
    backend/.venv/bin/python backend/export_questions.py --unlabelled theme

    # against Neon, same as the other backend scripts
    DATABASE_URL='postgresql+psycopg://...' backend/.venv/bin/python backend/export_questions.py

Writes {"id": ..., "text": ...} and nothing else.

The field is `text`, not `question`, because that is what everything
downstream reads - llm.py's Task.body(), pair_scores.py, deduplication.py.
Renaming it here would produce a file that looks right and silently labels
nothing.

"Latest" means most recent `period` (YYYY-MM), then highest id within a month,
which is the scrapers' own ordering.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import func              # noqa: E402

from app.db import SessionLocal          # noqa: E402
from app.models import Question          # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--limit", type=int, default=200,
                    help="how many, newest first (default 200; 0 = all)")
    ap.add_argument("-t", "--tache", type=int, choices=(2, 3),
                    help="only this tache. The pipeline works one tache at a "
                         "time, so mixing them is rarely what you want")
    ap.add_argument("--unlabelled", choices=("theme", "abstract", "core_subject"),
                    help="only rows where this column is still empty")
    ap.add_argument("--with", dest="extra", action="append", default=[],
                    choices=("theme", "abstract", "core_subject"),
                    help="include this column alongside id and text. llm.py's "
                         "core_subject task needs --with theme, because it "
                         "groups by it. Repeatable")
    ap.add_argument("--source", choices=("formation", "reussir", "opal"),
                        help="only rows from this source")
    ap.add_argument("--theme", action="append", default=[], metavar="NAME",
                    help="only rows with this theme. Repeatable. Use --list-themes "
                         "to see the exact spellings - they must match the "
                         "database, and llm_tasks.VOCABULARY, character for "
                         "character")
    ap.add_argument("--list-themes", action="store_true",
                    help="print the themes in the database with their counts, "
                         "then exit")
    ap.add_argument("-o", "--output", type=Path,
                    help="default: questions_<n>_<source>_<filters>.jsonl in the cwd")
    args = ap.parse_args()

    with SessionLocal() as db:
        if args.list_themes:
            rows = (db.query(Question.theme, func.count(Question.id))
                      .filter(Question.theme.isnot(None))
                      .group_by(Question.theme)
                      .order_by(func.count(Question.id).desc()).all())
            for theme, n in rows:
                print(f"  {n:5}  {theme}")
            print(f"\n  {len(rows)} theme(s); "
                  f"{db.query(Question).filter(Question.theme.is_(None)).count()} "
                  f"row(s) have none")
            return

        q = db.query(Question)
        if args.tache:
            q = q.filter(Question.tache == args.tache)
        if args.unlabelled:
            q = q.filter(getattr(Question, args.unlabelled).is_(None))
        # newest first: period is "YYYY-MM" so it sorts chronologically as text
        if args.source:
            q = q.filter(Question.source == args.source)
        if args.theme:
            q = q.filter(Question.theme.in_(args.theme))
        q = q.order_by(Question.period.desc(), Question.id.desc())
        if args.limit:
            q = q.limit(args.limit)
        rows = q.all()

    if not rows:
        sys.exit("no questions matched")

    bits = [str(len(rows))]
    if args.tache:
        bits.append(f"tache{args.tache}")
    if args.unlabelled:
        bits.append(f"no_{args.unlabelled}")
    if args.source:
        bits.append(f"{args.source}")
    if args.theme:
        # one theme names itself; several would make an unreadable filename
        bits.append(re.sub(r"[^A-Za-z0-9]+", "-", args.theme[0]).strip("-").lower()
                    if len(args.theme) == 1 else f"{len(args.theme)}themes")
    out = args.output or Path(f"questions_{'_'.join(bits)}.jsonl")
    with out.open("w", encoding="utf-8") as fh:
        for r in rows:
            row = {"id": r.id, "text": r.text}
            for col in args.extra:
                row[col] = getattr(r, col)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    # a grouped task cannot use rows whose grouping column is null
    if "theme" in args.extra:
        missing = sum(1 for r in rows if r.theme is None)
        if missing:
            print(f"  ! {missing} row(s) have no theme. llm.py's core_subject "
                  f"task groups by theme and will refuse them - label the "
                  f"themes first.", file=sys.stderr)

    periods = [r.period for r in rows]
    taches = sorted({r.tache for r in rows})
    print(f"wrote {len(rows)} question(s) to {out}")
    print(f"  periods {min(periods)} .. {max(periods)}")
    print(f"  tache   {taches}" + ("   <- mixed; pass --tache" if len(taches) > 1 else ""))


if __name__ == "__main__":
    main()
