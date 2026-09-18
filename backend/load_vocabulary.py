#!/usr/bin/env python3
"""Load a task's controlled vocabulary into its themes/core_subjects tables.

    backend/.venv/bin/python backend/load_vocabulary.py --dry-run
    backend/.venv/bin/python backend/load_vocabulary.py                # tache 2
    backend/.venv/bin/python backend/load_vocabulary.py --tache 3

    # against Neon, same as the other backend scripts
    DATABASE_URL='postgresql+psycopg://...' backend/.venv/bin/python backend/load_vocabulary.py

Task 2 and Task 3 share the themes and core_subjects tables and are kept
apart by themes.tache, so `--tache 3` can never disturb Task 2's rows: every
lookup and insert here is filtered by it.

The source of truth is questions_processing/llm_tasks.py's VOCABULARY dict -
the same object the core_subject prompt is built from. Keeping it there and
importing it here means the prompt and the database cannot disagree about what
a valid label is, which is the entire point of making core_subjects a table.

That dict is Task 2 only: its prompt opens "You are annotating TCF Canada
Speaking Task 2 role-play prompts". Task 3 is an opinion task and will want
its own themes, so VOCABULARIES below maps task -> vocabulary and currently
has one entry. Adding Task 3 means adding its dict to llm_tasks and a line
here - not editing the Task 2 list.

Idempotent: rows are matched on name (themes) and on (theme, name)
(core_subjects), so re-running after editing VOCABULARY adds what is new and
leaves the rest alone.

It never deletes. A label that disappears from VOCABULARY stays in the table,
because fingerprints may already point at it; removing one is a deliberate act
that has to deal with those rows first. Such labels are reported as `orphaned`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PROJECT))

from app.db import SessionLocal                        # noqa: E402
from app.models import CoreSubject, Theme              # noqa: E402

try:
    from questions_processing.llm_tasks import VOCABULARY   # noqa: E402
except ImportError as exc:                                  # pragma: no cover
    sys.exit(f"cannot import llm_tasks.VOCABULARY - {exc}")

# task -> {theme: [labels]}
VOCABULARIES = {2: VOCABULARY}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-t", "--tache", type=int, choices=(2, 3), default=2,
                    help="which task's vocabulary and tables (default 2)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change, write nothing")
    args = ap.parse_args()

    if args.tache not in VOCABULARIES:
        sys.exit(f"no vocabulary defined for tache {args.tache}. Add it to "
                 f"llm_tasks.py and map it in VOCABULARIES at the top of this "
                 f"file; do not reuse Task 2's, the tasks are different.")

    wanted = {theme: list(labels) for theme, labels in VOCABULARIES[args.tache].items()}
    print(f"tache {args.tache}: {len(wanted)} theme(s), "
          f"{sum(len(v) for v in wanted.values())} label(s)")

    with SessionLocal() as db:
        # scoped to this task throughout - a theme name may exist under both
        themes = {t.name: t for t in db.query(Theme)
                  .filter(Theme.tache == args.tache).all()}
        added_themes = []
        for name in wanted:
            if name not in themes:
                themes[name] = Theme(name=name, tache=args.tache)
                db.add(themes[name])
                added_themes.append(name)
        # flush, not commit: the new themes need ids before core_subjects can
        # reference them, but the whole run still succeeds or fails together
        db.flush()

        ours = {t.id for t in themes.values()}
        existing = {(cs.theme_id, cs.name) for cs in db.query(CoreSubject)
                    .filter(CoreSubject.theme_id.in_(ours)).all()}
        added_subjects = []
        for theme_name, labels in wanted.items():
            tid = themes[theme_name].id
            for label in labels:
                if (tid, label) not in existing:
                    db.add(CoreSubject(name=label, theme_id=tid))
                    existing.add((tid, label))
                    added_subjects.append(f"{theme_name} / {label}")

        # labels in the table that the vocabulary no longer lists - not an error,
        # but worth seeing, since nothing will ever be labelled with them again
        db.flush()
        by_id = {t.id: t.name for t in themes.values()}
        orphaned = [f"{by_id[cs.theme_id]} / {cs.name}"
                    for cs in db.query(CoreSubject)
                    .filter(CoreSubject.theme_id.in_(ours)).all()
                    if cs.name not in wanted.get(by_id[cs.theme_id], [])]

        verb = "would add" if args.dry_run else "added"
        print(f"\nthemes        {verb} {len(added_themes):3}")
        for n in added_themes:
            print(f"                + {n}")
        print(f"core_subjects {verb} {len(added_subjects):3}")
        for n in added_subjects[:10]:
            print(f"                + {n}")
        if len(added_subjects) > 10:
            print(f"                  ... and {len(added_subjects) - 10} more")
        if orphaned:
            print(f"\n! {len(orphaned)} label(s) in the table are not in the vocabulary:")
            for n in orphaned:
                print(f"    {n}")

        if args.dry_run:
            db.rollback()
            print("\ndry run - nothing written")
        else:
            db.commit()
            n_t = db.query(Theme).filter(Theme.tache == args.tache).count()
            n_cs = (db.query(CoreSubject)
                      .join(Theme).filter(Theme.tache == args.tache).count())
            print(f"\ncommitted. tache {args.tache}: "
                  f"themes {n_t}, core_subjects {n_cs}")


if __name__ == "__main__":
    main()
