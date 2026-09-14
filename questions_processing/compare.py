#!/usr/bin/env python3
"""
Compare the label files two or more models produced for the same questions.

Where the models agree, the question was unambiguous. Where they disagree,
something is unclear - and the useful move is to look at *which pair of labels*
clashed rather than at the individual rows, because a pair points at one line
in the prompt while a row points at nothing.

    # the headline: how often do they agree, and where do they clash?
    python3 compare.py questions_reussir/tache2_no_semantic_topic_*.jsonl

    # attach the question text, so the review file is readable
    python3 compare.py ..._topic_*.jsonl --source questions_reussir/tache2_no_semantic.jsonl

    # measure real accuracy against rows you labelled by hand
    python3 compare.py ..._topic_*.jsonl --gold my_50_labels.jsonl

Inputs are llm.py output files: one JSON object per line with `id`, the answer
field, and `model`. Two or more; agreement means *every* file agrees.

Output is a summary on stdout plus a disagreements file - one row per contested
question, carrying each model's label - for hand adjudication.

What agreement does and does not tell you
-----------------------------------------
Agreement means the question is unambiguous, NOT that the label is right. Two
models reading the same unclear rule make the same mistake and agree
confidently, so systematic error hides in the agreed set. That is what --gold
is for: it measures accuracy separately on the agreed and disagreed subsets,
which is the only way to know whether agreement is worth trusting here.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import textwrap
from pathlib import Path


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


def answer_field(rows: list[dict]) -> str:
    """Which key holds the answer - `topic`, `abstract`, whatever the task used.

    Inferred rather than configured: llm.py writes {id, <answer>, model}, so
    the answer is whatever is left once the two known keys are removed.
    """
    keys = {k for r in rows for k in r} - {"id", "model"}
    # a hand-built gold file usually keeps `text` alongside the label, because
    # you cannot label a question you cannot read. Ignore it when something
    # else is present, rather than making people strip it first.
    if len(keys) > 1:
        keys -= {"text", "note"}
    if len(keys) != 1:
        sys.exit(f"cannot tell which field is the answer, candidates: {sorted(keys)}")
    return keys.pop()


def load(path: Path) -> tuple[str, dict[str, str]]:
    """(label for this file, {id: answer})."""
    rows = read_jsonl(path)
    if not rows:
        sys.exit(f"{path} is empty")
    field = answer_field(rows)
    # the model that produced it names the column; fall back to the filename
    models = {r.get("model") for r in rows} - {None}
    name = models.pop() if len(models) == 1 else path.stem
    return name, {str(r["id"]): r[field] for r in rows}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path, help="two or more llm.py output files")
    ap.add_argument("--source", type=Path,
                    help="the questions JSONL, to attach `text` to each disagreement")
    ap.add_argument("--gold", type=Path,
                    help="hand-labelled rows, to measure accuracy rather than agreement")
    ap.add_argument("-o", "--output", type=Path,
                    help="disagreements file (default: beside the inputs, with "
                         "the _<task>_<model> tail replaced by _disagreements)")
    ap.add_argument("--top", type=int, default=10, help="label pairs to list (default 10)")
    args = ap.parse_args()

    if len(args.files) < 2:
        sys.exit("give at least two files - there is nothing to compare otherwise")

    # a glob can sweep up this script's own output; that is never an input
    own = [f for f in args.files
           if any("labels" in r for r in read_jsonl(f)[:1])]
    if own:
        for f in own:
            print(f"  ! skipping {f.name} - that is a compare.py output, not a "
                  f"model run", file=sys.stderr)
        args.files = [f for f in args.files if f not in own]
        if len(args.files) < 2:
            sys.exit("nothing left to compare once those are removed")

    runs = [load(f) for f in args.files]
    names = [n for n, _ in runs]
    if len(set(names)) != len(names):
        sys.exit(f"two inputs report the same model {names} - "
                 f"comparing a file with itself proves nothing")

    # only ids every run labelled: a row one model skipped is not a disagreement
    common = set.intersection(*(set(m) for _, m in runs))
    only = {n: len(m) - len(common) for n, m in runs}
    print(f"{len(common)} question(s) labelled by all {len(runs)} run(s)")
    for n, extra in only.items():
        if extra:
            print(f"  ! {n} has {extra} row(s) the others don't - ignored")
    if not common:
        sys.exit("no overlap: the runs covered different questions")

    agreed, disagreed = [], []
    for qid in sorted(common):
        labels = {n: m[qid] for n, m in runs}
        (agreed if len(set(labels.values())) == 1 else disagreed).append((qid, labels))

    rate = len(agreed) / len(common)
    print(f"\nagreement   {len(agreed):5} / {len(common)}   {rate:.1%}")
    print(f"disagree    {len(disagreed):5} / {len(common)}   {1 - rate:.1%}")

    # ---- the point of the exercise: group by which labels clashed ----------
    pairs = collections.Counter(
        tuple(sorted(set(labels.values()))) for _, labels in disagreed)
    if pairs:
        print(f"\ndisagreements by label pair (top {args.top}):")
        width = max(len(" vs ".join(p)) for p in pairs)
        running = 0
        for pair, n in pairs.most_common(args.top):
            running += n
            print(f"  {n:4}  {' vs '.join(pair):{width}}   {running / len(disagreed):5.0%} cumulative")
        print(f"\n  {len(pairs)} distinct pair(s). A large count is usually one "
              f"under-specified rule,\n  not many hard questions - fix the rule "
              f"and the whole cluster resolves.")

    # ---- which labels are stable, which are contested ---------------------
    involved = collections.Counter(l for _, labels in disagreed for l in set(labels.values()))
    total = collections.Counter(l for _, labels in agreed for l in labels.values())
    for _, labels in disagreed:
        total.update(set(labels.values()))
    if involved:
        print("\nper-label contest rate (share of its uses that were disputed):")
        for label, n in sorted(involved.items(), key=lambda kv: -kv[1] / total[kv[0]]):
            print(f"  {n:4} / {total[label]:4}  {n / total[label]:5.0%}  {label}")

    # the review queue and the blind-spot list both want the question text
    text = {}
    if args.source:
        text = {str(r["id"]): r.get("text", "") for r in read_jsonl(args.source)}

    # Drop the trailing _<task>_<model> so the result does not sit inside the
    # glob people naturally use for the inputs (..._topic_*.jsonl) - otherwise
    # the next run reads its own output back and cannot find an answer field.
    stem = args.files[0].stem
    base = "_".join(stem.split("_")[:-2]) or stem
    out = args.output or args.files[0].with_name(f"{base}_disagreements.jsonl")

    # ---- optional: what agreement is actually worth -----------------------
    if args.gold:
        grows = read_jsonl(args.gold)
        gfield = answer_field(grows)
        gold = {str(r["id"]): r[gfield] for r in grows}
        gold_text = {str(r["id"]): r["text"] for r in grows if r.get("text")}
        overlap = [(qid, labels) for qid, labels in agreed + disagreed if qid in gold]
        if not overlap:
            print(f"\n! --gold has no ids in common with the runs")
        else:
            def acc(rows):
                hits = sum(1 for qid, labels in rows
                           if any(v == gold[qid] for v in labels.values()))
                return hits, len(rows)

            ga = [(q, l) for q, l in overlap if len(set(l.values())) == 1]
            gd = [(q, l) for q, l in overlap if len(set(l.values())) > 1]
            print(f"\nagainst {len(overlap)} gold row(s):")
            for label, rows in (("agreed", ga), ("disagreed", gd)):
                if rows:
                    hit, n = acc(rows)
                    print(f"  {label:10} {hit:4} / {n:4}  {hit / n:5.0%} "
                          f"{'correct' if label == 'agreed' else 'had the right label somewhere'}")
            for name, mapping in runs:
                hit = sum(1 for qid, _ in overlap if mapping[qid] == gold[qid])
                print(f"  {name:24} {hit:4} / {len(overlap):4}  {hit / len(overlap):5.0%} alone")
            # Every model agreeing on the wrong answer is the one failure the
            # review queue cannot surface - those rows are in the *agreed*
            # pile, so nobody ever looks at them. Print them.
            blind = [(q, next(iter(l.values()))) for q, l in ga
                     if next(iter(l.values())) != gold[q]]
            if blind:
                print(f"\n! {len(blind)} row(s) where every run agreed and every run was "
                      f"wrong.\n  Systematic error: the rules said this, so it is a rule "
                      f"problem, not a\n  labelling problem. These never reach the review "
                      f"queue - they look settled.\n")
                by_swap = collections.Counter((gold[q], said) for q, said in blind)
                for (want, said), n in by_swap.most_common():
                    print(f"  {n:3}  you said {want!r} -> every run said {said!r}")
                shown = blind[:args.top]
                for i, (qid, said) in enumerate(shown, 1):
                    txt = gold_text.get(qid) or text.get(qid, "")
                    print(f"\n  {i}. {qid}   you: {gold[qid]}   runs: {said}")
                    if txt:
                        for line in textwrap.wrap(txt, 92, initial_indent="     ",
                                                  subsequent_indent="     "):
                            print(line)
                if len(blind) > len(shown):
                    print(f"\n  ... and {len(blind) - len(shown)} more (raise --top)")

                bs = out.with_name(f"{base}_blindspots.jsonl")
                with bs.open("w", encoding="utf-8") as fh:
                    for qid, said in blind:
                        row = {"id": qid, "gold": gold[qid], "agreed": said}
                        t = gold_text.get(qid) or text.get(qid, "")
                        if t:
                            row["text"] = t
                        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(f"\n  wrote them to {bs}")

    # ---- the review queue --------------------------------------------------
    with out.open("w", encoding="utf-8") as fh:
        for qid, labels in sorted(disagreed,
                                  key=lambda kl: -pairs[tuple(sorted(set(kl[1].values())))]):
            row = {"id": qid, "pair": sorted(set(labels.values())), "labels": labels}
            if text:
                row["text"] = text.get(qid, "")
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"\nwrote {len(disagreed)} disagreement(s) to {out}")
    if not text:
        print("  (pass --source to include the question text)")
    print("  ordered by pair size, so the biggest cluster is at the top")


if __name__ == "__main__":
    main()
