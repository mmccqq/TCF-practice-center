#!/usr/bin/env python3
"""Pull questions out of the database as JSONL, for the labelling tools.

    backend/.venv/bin/python backend/export_questions.py            # latest 200
    backend/.venv/bin/python backend/export_questions.py -n 50 --tache 2
    backend/.venv/bin/python backend/export_questions.py --unlabelled theme
    backend/.venv/bin/python backend/export_questions.py --unlabelled core_subject --with theme

    # against Neon, same as the other backend scripts
    DATABASE_URL='postgresql+psycopg://...' backend/.venv/bin/python backend/export_questions.py

Reads `fingerprints` - one row per unique question. That is deliberately not
`list_questions`: a question that recurred in eight months is one thing to
label, not eight, so exporting the list layer would multiply the LLM bill by
2.4x and invite eight copies of one question getting different labels.

Writes {"id": ..., "text": ...} and nothing else unless asked. The `id` is a
fingerprint id, which is what load_labels.py expects back.

The field is `text`, not `question`, because that is what everything
downstream reads - llm.py's Task.body(), pair_scores.py, deduplication.py.
Renaming it here would produce a file that looks right and silently labels
nothing.

"Latest" means most recently seen (`last_seen`), then highest id.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select                  # noqa: E402

from app.db import SessionLocal                      # noqa: E402
from app.models import (CoreSubject, Fingerprint,    # noqa: E402
                        RawQuestion, Theme)

# the label columns, and how to read each one's display value
LABELS = ("theme", "abstract", "core_subject")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--limit", type=int, default=200,
                    help="how many, most recently seen first (default 200; 0 = all)")
    ap.add_argument("-t", "--tache", type=int, choices=(2, 3),
                    help="only this tache. The pipeline works one tache at a "
                         "time, so mixing them is rarely what you want")
    ap.add_argument("--unlabelled", choices=LABELS,
                    help="only rows where this label is still empty")
    ap.add_argument("--with", dest="extra", action="append", default=[],
                    choices=LABELS,
                    help="include this label alongside id and text. llm.py's "
                         "core_subject task needs --with theme, because it "
                         "groups by it. Repeatable")
    ap.add_argument("--source", choices=("formation", "reussir", "opal"),
                    help="only questions with at least one sighting from this "
                         "source. A question can have sightings from several")
    ap.add_argument("--theme", action="append", default=[], metavar="NAME",
                    help="only rows with this theme. Repeatable. Use --list-themes "
                         "to see the exact spellings")
    ap.add_argument("--list-themes", action="store_true",
                    help="print the themes in the database with their counts, "
                         "then exit")
    ap.add_argument("-o", "--output", type=Path,
                    help="default: questions_<n>_<filters>.jsonl in the cwd")
    args = ap.parse_args()

    with SessionLocal() as db:
        if args.list_themes:
            rows = db.execute(
                select(Theme.name, Theme.tache, func.count())
                .join(Fingerprint, Fingerprint.theme_id == Theme.id)
                .group_by(Theme.name, Theme.tache)
                .order_by(func.count().desc())).all()
            for name, tache, n in rows:
                print(f"  {n:5}  [t{tache}]  {name}")
            unlabelled = db.scalar(select(func.count()).select_from(Fingerprint)
                                   .where(Fingerprint.theme_id.is_(None)))
            print(f"\n  {len(rows)} theme(s) in use; {unlabelled} question(s) have none")
            return

        q = (select(Fingerprint.id, Fingerprint.text, Fingerprint.abstract,
                    Fingerprint.first_seen, Fingerprint.last_seen,
                    Fingerprint.tache,
                    Theme.name.label("theme"),
                    CoreSubject.name.label("core_subject"))
             .outerjoin(Theme, Theme.id == Fingerprint.theme_id)
             .outerjoin(CoreSubject, CoreSubject.id == Fingerprint.core_subject_id))

        if args.tache:
            q = q.where(Fingerprint.tache == args.tache)
        if args.unlabelled == "abstract":
            q = q.where(Fingerprint.abstract.is_(None))
        elif args.unlabelled == "theme":
            q = q.where(Fingerprint.theme_id.is_(None))
        elif args.unlabelled == "core_subject":
            q = q.where(Fingerprint.core_subject_id.is_(None))
        if args.source:
            # EXISTS, not a join: a question with three sightings from one
            # source would otherwise come back three times
            q = q.where(select(RawQuestion.id)
                        .where(RawQuestion.f_id == Fingerprint.id,
                               RawQuestion.source == args.source)
                        .exists())
        if args.theme:
            q = q.where(Theme.name.in_(args.theme))

        q = q.order_by(Fingerprint.last_seen.desc(), Fingerprint.id.desc())
        if args.limit:
            q = q.limit(args.limit)
        rows = db.execute(q).all()

    if not rows:
        sys.exit("no questions matched")

    bits = [str(len(rows))]
    if args.tache:
        bits.append(f"tache{args.tache}")
    if args.unlabelled:
        bits.append(f"no_{args.unlabelled}")
    if args.source:
        bits.append(args.source)
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

    seen = [r.last_seen for r in rows if r.last_seen]
    taches = sorted({r.tache for r in rows})
    print(f"wrote {len(rows)} question(s) to {out}")
    if seen:
        print(f"  last seen {min(seen)} .. {max(seen)}")
    print(f"  tache     {taches}" + ("   <- mixed; pass --tache" if len(taches) > 1 else ""))


if __name__ == "__main__":
    main()
