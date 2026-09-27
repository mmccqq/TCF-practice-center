#!/usr/bin/env python3
"""
Every pairwise cosine score for a set of questions. No clustering, no merging.

    # all pairs, TF-IDF, to a TSV you can sort in Excel
    python3 pair_scores.py gold_set/tache2_gold.jsonl > scores.tsv

    # the same with an embedding model, local or hosted. Vectors are cached
    # per model, so a second run at a different --min re-embeds nothing.
    python3 pair_scores.py gold_set/tache2_gold.jsonl \\
        --mode embed -m Qwen/Qwen3-Embedding-0.6B > qwen.tsv
    python3 pair_scores.py gold_set/tache2_gold.jsonl \\
        --mode embed -m openai:text-embedding-3-large > openai.tsv

    # just the interesting end, with the question text alongside
    python3 pair_scores.py questions_reussir/tache2.jsonl --min 0.7 --text

    # a hand-review queue: text, theme and abstract on both sides, opened in
    # review.html. The admin Labelling tab's export already carries all three,
    # so one input is enough (xlsx_to_jsonl.py converts the workbook).
    python3 pair_scores.py questions_1512_tache2.jsonl \\
        --min 0.75 --review -o tache2_pairs_review.jsonl

    # when the text and the labels are in different files - the scraper writes
    # {id, text, ...} and llm.py writes {id, theme, model} - pass both and they
    # are merged by id
    python3 pair_scores.py questions_reussir/tache2.jsonl \\
        questions_reussir_theme_gpt.jsonl --min 0.75 --review

This exists to answer one question: what do this model's scores actually look
like on this corpus? deduplication.py's thresholds are meaningless until you
have seen that distribution, and they differ per model - e5-large pinned every
pair into [0.81, 1.00] while TF-IDF put the median at 0.03.

The vectors come from deduplication.py's own tfidf()/embed(), and the text is
run through the same normalize(), so a score here is exactly the score the
dedup pipeline would compute. Reimplementing it would defeat the purpose.

Output is one line per unordered pair, strongest first. Note that N questions
give N*(N-1)/2 pairs, not N*N: a pair is counted once and nothing is compared
with itself. 100 questions -> 4,950 lines; 1,000 -> 499,500. Set --min or --top
before --review, or the queue is every pair in the corpus.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deduplication import EMBED_MODEL, TFIDF_NGRAM, embed, normalize, tfidf  # noqa: E402


# Carried through to the output but never scored: the vectors come from the
# text alone, exactly as deduplication.py computes them. A reviewer deciding
# whether two questions are the same still wants to see what each was labelled -
# two sides under different themes is a reason to look twice before merging.
CARRIED = ("theme", "abstract")


def read(paths: list[Path]) -> list[dict]:
    """Every input merged into one row per id.

    Several files rather than one, because the text and the labels are written
    by different steps: the scraper emits {id, text, ...} and llm.py emits
    {id, theme, model}. Merging on id fills in one row from both; appending per
    line - what this did before - produced two half-rows, and the label-only
    half was then dropped for having no text.

    Later files win on a field they set, so a hand-corrected file can be passed
    after the model output it corrects.
    """
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for path in paths:
        if not path.exists():
            sys.exit(f"not found: {path}")
        with path.open(encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError as exc:
                    sys.exit(f"{path}:{n}: invalid JSON - {exc}")
                if d.get("id") is None:
                    continue
                rid = str(d["id"])
                row = by_id.get(rid)
                if row is None:
                    row = by_id[rid] = {"id": rid}
                    order.append(rid)
                if d.get("text"):
                    row["text"] = d["text"]
                for field in CARRIED:
                    value = d.get(field)
                    if value not in (None, ""):
                        row[field] = str(value)

    rows = [by_id[i] for i in order if by_id[i].get("text")]
    if len(rows) < len(order):
        # a label file naming questions no input carried the text for is a
        # mismatched pair of files, not a detail to swallow
        print(f"{len(order) - len(rows)} id(s) had no text in any input - skipped",
              file=sys.stderr)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="+", type=Path, help="JSONL with `id` and `text`")
    ap.add_argument("--mode", choices=("tfidf", "embed"), default="tfidf")
    ap.add_argument("-m", "--model", default=EMBED_MODEL,
                    help="--mode embed only: a sentence-transformers model, or "
                         "openai:<model> for the hosted endpoint")
    ap.add_argument("--ngram", type=int, default=TFIDF_NGRAM, help="--mode tfidf only")
    ap.add_argument("--min", type=float, default=0.0, metavar="X",
                    help="only pairs scoring at least this (default: all)")
    ap.add_argument("--top", type=int, default=None, metavar="N",
                    help="only the N highest-scoring pairs")
    ap.add_argument("--text", action="store_true", help="include both question texts")
    ap.add_argument("--jsonl", action="store_true", help="JSONL instead of TSV")
    ap.add_argument("--review", action="store_true",
                    help="write the shape review.html's dedup queue reads "
                         "(implies --jsonl --text)")
    ap.add_argument("-o", "--output", type=Path, help="default: stdout")
    args = ap.parse_args()

    # review.html decides its mode from the first line's keys and diffs `a`
    # against `b`, so a review file without the texts renders two empty boxes.
    # One flag rather than two remembered ones.
    if args.review:
        args.jsonl = args.text = True

    rows = read(args.input)
    n = len(rows)
    if n < 2:
        sys.exit(f"need at least two questions, found {n}")
    print(f"{n} questions -> {n * (n - 1) // 2:,} pairs", file=sys.stderr)

    import numpy as np

    texts = [normalize(r["text"]) for r in rows]
    vecs = tfidf(texts, args.ngram) if args.mode == "tfidf" else embed(texts, args.model)

    # both vectorisers return L2-normalised rows, so the dot product IS cosine
    sim = vecs @ vecs.T
    iu = np.triu_indices(n, k=1)          # upper triangle: each pair once
    scores = sim[iu]

    pct = np.percentile(scores, [50, 90, 99, 99.9])
    print(f"min={scores.min():.4f}  p50={pct[0]:.4f}  p90={pct[1]:.4f}  "
          f"p99={pct[2]:.4f}  p99.9={pct[3]:.4f}  max={scores.max():.4f}",
          file=sys.stderr)

    keep = np.where(scores >= args.min)[0]
    keep = keep[np.argsort(-scores[keep])]        # strongest first
    if args.top:
        keep = keep[: args.top]
    print(f"writing {len(keep):,} pair(s) scoring >= {args.min}", file=sys.stderr)

    # Only the label fields some input actually supplied. Deciding once, from
    # the whole corpus, keeps every line of one file the same shape - a TSV
    # needs that to stay a table, and it keeps the JSONL keys predictable.
    carried = [f for f in CARRIED if any(f in r for r in rows)]
    if args.review and not carried:
        print("note: no theme or abstract in any input - pass the label file too",
              file=sys.stderr)

    fh = args.output.open("w", encoding="utf-8") if args.output else sys.stdout
    try:
        if not args.jsonl:
            # paired column by column (a_theme, b_theme) rather than side by
            # side, so a spreadsheet can sort on one side and read across
            cols = (["score", "a_id", "b_id"]
                    + [f"{side}_{f}" for f in carried for side in ("a", "b")]
                    + (["a", "b"] if args.text else []))
            print("\t".join(cols), file=fh)
        for k in keep:
            i, j = int(iu[0][k]), int(iu[1][k])
            score = round(float(scores[k]), 4)
            a, b = rows[i], rows[j]
            if args.jsonl:
                rec = {"score": score, "a_id": a["id"], "b_id": b["id"]}
                for f in carried:
                    rec[f"a_{f}"], rec[f"b_{f}"] = a.get(f, ""), b.get(f, "")
                if args.text:
                    rec["a"], rec["b"] = a["text"], b["text"]
                print(json.dumps(rec, ensure_ascii=False), file=fh)
            else:
                # tabs stripped from every cell, or the columns would not line up
                cells = [f"{score:.4f}", a["id"], b["id"]]
                for f in carried:
                    cells += [a.get(f, ""), b.get(f, "")]
                if args.text:
                    cells += [a["text"], b["text"]]
                print("\t".join(c.replace("\t", " ") for c in cells), file=fh)
    finally:
        if args.output:
            fh.close()


if __name__ == "__main__":
    main()
