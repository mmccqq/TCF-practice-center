#!/usr/bin/env python3
"""Move the labels off `questions` and onto `fingerprints`. One-shot.

    backend/.venv/bin/python backend/transfer_labels.py --dry-run
    backend/.venv/bin/python backend/transfer_labels.py

    # against Neon, same as the other backend scripts
    DATABASE_URL='postgresql+psycopg://...' backend/.venv/bin/python backend/transfer_labels.py

Part of step 4 of "Core question set design.md". `questions.theme` /
`.abstract` / `.core_subject` are strings on a table that is going away; the
new home is `fingerprints`, where one row is one question no matter how many
months it recurs in, so every month's copy shows the same label by
construction.

This is a migration, not a pipeline: it runs once, or again after a re-run of
the labelling. Future labelling should write to `fingerprints` directly - see
the note at the bottom about load_labels.py.

Matching on the scraper id, not the text
----------------------------------------
`questions.id` IS a scraper id, and `raw_questions` holds every scraper id, so
the join is exact. Text is only a fallback. That matters because 45 `questions`
rows hold wording that predates the scraper fixes (the "->" marker, the
multi-<strong> truncation) and no longer hashes to anything the scrapers
produce - 40 of them labelled. Measured on this corpus: id matches 598/598,
text matches 558/598, and where both resolve they never disagree.

Strings become foreign keys
---------------------------
`theme` and `core_subject` resolve to `themes.id` and `core_subjects.id`. A
name that is not in the vocabulary cannot be written - that is the drift guard
working, not a bug. Those rows are reported so you can decide whether the label
belongs in llm_tasks.VOCABULARY (then re-run load_vocabulary.py and this
script) or was a mistake.
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PROJECT))

from app.db import SessionLocal                                   # noqa: E402
from app.models import (CoreSubject, Fingerprint, Question,       # noqa: E402
                        RawQuestion, Theme)

from questions_processing.deduplication import fingerprint as fp_of   # noqa: E402

FIELDS = ("theme", "abstract", "core_subject")


def key(name: str) -> str:
    """Lookup form of a label name.

    Case-, whitespace- and apostrophe-insensitive, because
    `questions.core_subject` holds whatever the LLM and the review tool wrote
    and the vocabulary has since been normalised: "Restaurant opening event"
    must still find "restaurant opening event", and a curly U+2019 must find
    the straight quote the vocabulary now uses. Matches what new_labels.py
    does, plus the quote.
    """
    return " ".join(name.replace("\u2019", "'").split()).casefold()


def completeness(q: Question) -> tuple:
    """How much a row has to say, for picking between rows that collide.

    Several `questions` rows can map to one fingerprint - the cross-month
    duplicates the old model left behind. Prefer the row with the most labels
    filled, then the most recent one.
    """
    return (sum(1 for f in FIELDS if getattr(q, f)), q.period, q.id)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change, write nothing")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace values already set on fingerprints (default: "
                         "keep them, so a rerun cannot undo later hand edits)")
    args = ap.parse_args()

    with SessionLocal() as db:
        rows = db.query(Question).filter(
            (Question.theme.isnot(None)) | (Question.abstract.isnot(None))
            | (Question.core_subject.isnot(None))).all()
        if not rows:
            sys.exit("no labelled rows in `questions` - nothing to transfer")
        print(f"{len(rows)} labelled questions row(s)")

        # ---- resolve each row to a fingerprint ----
        by_raw = {r.id: r.f_id for r in db.query(RawQuestion).all()}
        by_text = {(f.tache, f.fingerprint): f.id for f in db.query(Fingerprint).all()}

        stats = collections.Counter()
        claims: dict[int, list[Question]] = collections.defaultdict(list)
        for q in rows:
            if q.id in by_raw:
                stats["by id"] += 1
                claims[by_raw[q.id]].append(q)
            elif (fp_key := (q.tache, fp_of(q.text))) in by_text:
                stats["by text"] += 1
                claims[by_text[fp_key]].append(q)
            else:
                stats["unmatched"] += 1
        print(f"  {stats['by id']} matched by scraper id, {stats['by text']} by text, "
              f"{stats['unmatched']} unmatched")

        # ---- several rows claiming one fingerprint ----
        conflicts = []
        winners: dict[int, Question] = {}
        for f_id, qs in claims.items():
            if len(qs) > 1:
                distinct = {tuple(getattr(q, f) for f in FIELDS) for q in qs}
                if len(distinct) > 1:
                    conflicts.append((f_id, qs))
            winners[f_id] = max(qs, key=completeness)
        if conflicts:
            print(f"\n! {len(conflicts)} fingerprint(s) claimed by rows that disagree; "
                  f"kept the most complete, most recent:")
            for f_id, qs in conflicts:
                chosen = winners[f_id]
                # say which field actually differs - most of these collide only
                # on `abstract`, which is a wording difference and not worth a
                # second look; a differing theme is
                differ = [f for f in FIELDS if len({getattr(q, f) for q in qs}) > 1]
                print(f"    f_id={f_id}  differs on: {', '.join(differ)}")
                for q in sorted(qs, key=completeness, reverse=True):
                    mark = "keep" if q is chosen else "drop"
                    bits = "  ".join(f"{f}={getattr(q, f)!r}" for f in differ)
                    print(f"      {mark}  {q.period} {q.id}  {bits}")

        # ---- resolve the strings to foreign keys ----
        theme_id = {(t.tache, key(t.name)): t.id for t in db.query(Theme).all()}
        cs_id = {(c.theme_id, key(c.name)): c.id for c in db.query(CoreSubject).all()}

        unresolved = collections.Counter()
        changed = collections.Counter()
        kept = collections.Counter()
        fps = {f.id: f for f in db.query(Fingerprint)
               .filter(Fingerprint.id.in_(winners)).all()}

        for f_id, q in winners.items():
            f = fps[f_id]
            new: dict = {}
            # best row first, so a value is taken from a lesser claimant only
            # when the best one has nothing usable to offer for that field
            ranked = sorted(claims[f_id], key=completeness, reverse=True)

            if q.theme:
                tid = theme_id.get((q.tache, key(q.theme)))
                if tid is None:
                    unresolved[f"theme {q.theme!r}"] += 1
                else:
                    new["theme_id"] = tid
            if q.abstract:
                new["abstract"] = q.abstract

            # core_subject is resolved per field rather than per row: the
            # most-complete row can hold a label that is not in the vocabulary
            # while a lesser claimant holds one that is, and dropping the good
            # one because it lost a tie-break would be silly. Only rows whose
            # theme matches the theme being written are eligible, or the label
            # would belong to a different theme than the question.
            chosen_theme = new.get("theme_id") or f.theme_id
            tried = []
            for cand in ranked:
                if not cand.core_subject:
                    continue
                if theme_id.get((cand.tache, key(cand.theme or ""))) != chosen_theme:
                    continue
                cid = cs_id.get((chosen_theme, key(cand.core_subject)))
                if cid is not None:
                    new["core_subject_id"] = cid
                    if cand is not q:
                        stats["core_subject from a lesser claimant"] += 1
                    break
                tried.append(cand)
            else:
                for cand in tried:
                    unresolved[f"core_subject {cand.core_subject!r} "
                               f"under {cand.theme!r}"] += 1

            for col, value in new.items():
                old = getattr(f, col)
                if old == value:
                    kept[col] += 1
                elif old is not None and not args.overwrite:
                    kept[col] += 1
                    changed[f"{col} left alone"] += 1
                else:
                    setattr(f, col, value)
                    changed[col] += 1

        print()
        if stats["core_subject from a lesser claimant"]:
            print(f"  {stats['core_subject from a lesser claimant']} core_subject(s) "
                  f"taken from a lesser claimant whose label was in the vocabulary")
        for col in ("theme_id", "abstract", "core_subject_id"):
            verb = "would set" if args.dry_run else "set"
            print(f"  {changed[col]:4} {verb} fingerprints.{col}"
                  + (f"   ({changed[col + ' left alone']} already set, "
                     f"use --overwrite)" if changed[col + " left alone"] else ""))

        if unresolved:
            total = sum(unresolved.values())
            print(f"\n! {total} label(s) across {len(unresolved)} name(s) are not in "
                  f"the vocabulary, so they were skipped:")
            for name, n in unresolved.most_common():
                print(f"    {n:3}  {name}")
            print("  add the ones worth keeping to llm_tasks.VOCABULARY, re-run "
                  "load_vocabulary.py, then re-run this script")

        if args.dry_run:
            db.rollback()
            print("\ndry run - nothing written")
            return

        db.commit()
        n = db.query(Fingerprint).count()
        print("\ncommitted.")
        for col in ("theme_id", "abstract", "core_subject_id"):
            k = db.query(Fingerprint).filter(
                getattr(Fingerprint, col).isnot(None)).count()
            print(f"  fingerprints.{col:16} {k:5} / {n} filled  ({k / n:.0%})")


if __name__ == "__main__":
    main()
