#!/usr/bin/env python3
"""Which core subjects are new, i.e. not in their theme's vocabulary.

    python3 new_labels.py ../backend/..._core_subject_gpt-5.6-luna.jsonl \
        --source ../backend/questions_587_tache2_no_core_subject_reussir.jsonl

    # write a copy carrying new_label, for review or for the record
    python3 new_labels.py ... --source ... -o labelled.jsonl

The model is never asked whether a label is new. It cannot be trusted to
know - an earlier prompt had it answer "new_label": false while using a label
that was not in the list at all. The answer is computable exactly: compare
what came back against VOCABULARY[theme]. Computed beats self-reported.

A cluster of new labels inside one theme usually means one of two things: the
vocabulary has a real gap, or those rows are mis-themed and were shown the
wrong list.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm_tasks import VOCABULARY          # noqa: E402


def read(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"not found: {path}")
    out = []
    for n, line in enumerate(path.open(encoding="utf-8"), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:
            sys.exit(f"{path}:{n}: invalid JSON - {exc}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("answers", type=Path, help="llm.py core_subject output")
    ap.add_argument("--source", type=Path, required=True,
                    help="the input file, which carries `theme` per id")
    ap.add_argument("-o", "--output", type=Path,
                    help="write a copy with new_label added")
    args = ap.parse_args()

    theme_of = {str(r["id"]): r.get("theme") for r in read(args.source)}
    rows = read(args.answers)

    # case and spacing are not a new label, just a sloppy one
    known = {t: {v.strip().casefold(): v for v in vs} for t, vs in VOCABULARY.items()}

    out, new, drift, unthemed = [], collections.defaultdict(list), [], 0
    for r in rows:
        subject = r.get("core_subject", "")
        theme = theme_of.get(str(r["id"]))
        if theme is None:
            unthemed += 1
            continue
        table = known.get(theme, {})
        match = table.get(subject.strip().casefold())
        is_new = match is None
        if match is not None and match != subject:
            drift.append((theme, subject, match))
        if is_new:
            new[theme].append(subject)
        out.append({**r, "new_label": is_new})

    total_new = sum(len(v) for v in new.values())
    print(f"{len(rows)} answer(s), {total_new} new label(s) "
          f"({total_new / max(len(rows), 1):.0%})")
    if unthemed:
        print(f"  ! {unthemed} row(s) had no theme in --source, skipped")

    if new:
        print("\nnew labels by theme (vocabulary size in brackets):")
        for theme, labels in sorted(new.items(), key=lambda kv: -len(kv[1])):
            counts = collections.Counter(labels)
            print(f"\n  {len(labels):3} new / {len(VOCABULARY.get(theme, []))} known"
                  f"   {theme}")
            for label, n in counts.most_common():
                print(f"        {n:3}x  {label}")

    if drift:
        print("\nmatched the vocabulary only after ignoring case/spacing:")
        for theme, got, want in sorted(set(drift))[:10]:
            print(f"  {got!r} -> {want!r}   ({theme})")
        print("  these were counted as known, not new")

    if args.output:
        with args.output.open("w", encoding="utf-8") as fh:
            for r in out:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\nwrote {len(out)} row(s) to {args.output}")


if __name__ == "__main__":
    main()
