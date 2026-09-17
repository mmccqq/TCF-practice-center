#!/usr/bin/env python3
"""
Every pairwise cosine score for a set of questions. No clustering, no merging.

    # all pairs, TF-IDF, to a TSV you can sort in Excel
    python3 pair_scores.py gold_set/tache2_gold.jsonl > scores.tsv

    # the same with an embedding model
    python3 pair_scores.py gold_set/tache2_gold.jsonl \\
        --mode embed -m Qwen/Qwen3-Embedding-0.6B > qwen.tsv

    # just the interesting end, with the question text alongside
    python3 pair_scores.py questions_reussir/tache2.jsonl --min 0.7 --text

This exists to answer one question: what do this model's scores actually look
like on this corpus? deduplication.py's thresholds are meaningless until you
have seen that distribution, and they differ per model - e5-large pinned every
pair into [0.81, 1.00] while TF-IDF put the median at 0.03.

The vectors come from deduplication.py's own tfidf()/embed(), and the text is
run through the same normalize(), so a score here is exactly the score the
dedup pipeline would compute. Reimplementing it would defeat the purpose.

Output is one line per unordered pair, strongest first. Note that N questions
give N*(N-1)/2 pairs, not N*N: a pair is counted once and nothing is compared
with itself. 100 questions -> 4,950 lines; 1,000 -> 499,500.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deduplication import EMBED_MODEL, TFIDF_NGRAM, embed, normalize, tfidf  # noqa: E402


def read(paths: list[Path]) -> list[dict]:
    rows = []
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
                if d.get("id") is not None and d.get("text"):
                    rows.append({"id": str(d["id"]), "text": d["text"]})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="+", type=Path, help="JSONL with `id` and `text`")
    ap.add_argument("--mode", choices=("tfidf", "embed"), default="tfidf")
    ap.add_argument("-m", "--model", default=EMBED_MODEL, help="--mode embed only")
    ap.add_argument("--ngram", type=int, default=TFIDF_NGRAM, help="--mode tfidf only")
    ap.add_argument("--min", type=float, default=0.0, metavar="X",
                    help="only pairs scoring at least this (default: all)")
    ap.add_argument("--top", type=int, default=None, metavar="N",
                    help="only the N highest-scoring pairs")
    ap.add_argument("--text", action="store_true", help="include both question texts")
    ap.add_argument("--jsonl", action="store_true", help="JSONL instead of TSV")
    ap.add_argument("-o", "--output", type=Path, help="default: stdout")
    args = ap.parse_args()

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

    fh = args.output.open("w", encoding="utf-8") if args.output else sys.stdout
    try:
        if not args.jsonl:
            cols = ["score", "a_id", "b_id"] + (["a", "b"] if args.text else [])
            print("\t".join(cols), file=fh)
        for k in keep:
            i, j = int(iu[0][k]), int(iu[1][k])
            score = round(float(scores[k]), 4)
            if args.jsonl:
                rec = {"score": score, "a_id": rows[i]["id"], "b_id": rows[j]["id"]}
                if args.text:
                    rec["a"], rec["b"] = rows[i]["text"], rows[j]["text"]
                print(json.dumps(rec, ensure_ascii=False), file=fh)
            else:
                # tabs stripped from the text, or the columns would not line up
                cells = [f"{score:.4f}", rows[i]["id"], rows[j]["id"]]
                if args.text:
                    cells += [rows[i]["text"].replace("\t", " "),
                              rows[j]["text"].replace("\t", " ")]
                print("\t".join(cells), file=fh)
    finally:
        if args.output:
            fh.close()


if __name__ == "__main__":
    main()
