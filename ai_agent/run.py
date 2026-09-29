#!/usr/bin/env python3
"""Run the labelling agent over the question bank. Local, report-only.

    export OPENAI_API_KEY=...

    # 1. one-time: embed the corpus (about 1,541 tache-2 questions)
    python3 -m ai_agent.run backfill

    # 2. the smoke run - 20 questions, read every trace by hand
    python3 -m ai_agent.run run --limit 20 -o out/smoke

    # 3. the full run
    python3 -m ai_agent.run run -o out/run1

    # offline: prompts, schemas and slot extraction, no API call, no cost
    python3 -m ai_agent.run selftest

Targets whichever database DATABASE_URL points at, so production is
    DATABASE_URL='postgresql+psycopg://...' python3 -m ai_agent.run run
exactly as it is for alembic.

OUTPUT
    <out>.jsonl          one full record per question: the decision, the
                         evidence, the reasoning, the trace, the token counts
    <out>_labels.jsonl   {id, core_subject, model} - the thin shape
                         load_labels.py, compare.py, review.html and the admin
                         upload already read. The agent inherits the format
                         rather than inventing one.

Nothing is written to the question bank. This produces a report; a human
applies it.

RESUME
    Re-running skips ids already in <out>.jsonl, so a run killed at question
    400 of 696 costs 296 questions on the retry, not 696. Same contract as
    llm.py.

CAPS
    Per question: --max-steps, --max-tokens, and at most 4 find_similar calls.
    Per run:      --limit, --budget-tokens, --max-minutes.
    A cap is a runaway guard, not a size estimate. The per-question token
    counts are logged so the real cost can be read off run 1 and the caps
    tightened from data rather than guessed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import store
from .agent import Config
from .tools import breakdown_slots


# ------------------------------------------------------------------ output --

def read_done(path: Path) -> set[int]:
    if not path.exists():
        return set()
    done = set()
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                done.add(json.loads(line)["f_id"])
            except Exception:                        # noqa: BLE001
                pass
    return done


def append(path: Path, row: dict) -> None:
    with path.open("a") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def report(rows: list[dict], model: str, seconds: float) -> None:
    n = len(rows)
    if not n:
        print("nothing to report")
        return
    checked = [r for r in rows if r["current_core_subject"]]
    agreed = [r for r in checked if r["agrees_with_bank"]]
    submitted = [r for r in rows if r["action"] == "submit_decision"]
    toks = sum(r["total_tokens"] for r in rows)
    calls = sum(r["model_calls"] for r in rows)

    print(f"\n{n} questions · {calls} model calls · {toks:,} tokens · "
          f"{seconds/60:.1f} min · {model}")
    if checked:
        print(f"  agreed with bank      {len(agreed):>4} / {len(checked)}"
              f"   {len(agreed)/len(checked)*100:.1f}%")
        print(f"  revised the bank      {len(checked)-len(agreed):>4}")
    print(f"  new subject proposed  {sum(1 for r in submitted if r['is_new']):>4}")
    print(f"  deferred              {sum(1 for r in rows if r['action']=='defer_to_human'):>4}")
    print(f"  no decision (capped)  {sum(1 for r in rows if r['action'] is None):>4}")
    print(f"  theme looks wrong     {sum(1 for r in rows if r['theme_looks_wrong']):>4}")
    print(f"  fabricated citations  {sum(1 for r in submitted if not r['evidence_ok']):>4}")
    print(f"\n  tokens/question  median "
          f"{sorted(r['total_tokens'] for r in rows)[n//2]:,}"
          f"   max {max(r['total_tokens'] for r in rows):,}")
    print(f"  calls/question   median "
          f"{sorted(r['model_calls'] for r in rows)[n//2]}"
          f"   max {max(r['model_calls'] for r in rows)}")

    # what any review should start with: the disagreements, most confident
    # first - the same thing jev_labels.py's report prints, for the same reason
    wrong = [r for r in checked if r["agrees_with_bank"] is False]
    if wrong:
        print(f"\n  most confident disagreements with the bank:")
        for r in sorted(wrong, key=lambda r: -(r["confidence"] or 0))[:10]:
            print(f"    {r['confidence']:.2f}  f_id {r['f_id']:<6} "
                  f"bank {r['current_core_subject']!r:32} "
                  f"agent {r['agent_core_subject']!r}")


# ----------------------------------------------------------------- commands --

def cmd_backfill(a) -> None:
    db = store.session()
    n = store.backfill(db, model=a.embed_model, source=a.source,
                       tache=a.tache, limit=a.limit)
    print(f"embedded {n}")


def cmd_run(a) -> None:
    from openai import OpenAI

    # -o takes a stem; tolerate "run1.jsonl" so the two names never come out
    # as run1.jsonl and run1.jsonl_labels.jsonl
    out = Path(a.output)
    if out.suffix == ".jsonl":
        out = out.with_suffix("")
    out.parent.mkdir(parents=True, exist_ok=True)
    full, thin = Path(f"{out}.jsonl"), Path(f"{out}_labels.jsonl")

    db = store.session()
    index = store.EmbeddingIndex(a.embed_model, a.source, a.tache)
    index.load(db)
    if index.size == 0:
        sys.exit(f"no embeddings for ({a.embed_model}, {a.source}). "
                 f"Run:  python3 -m ai_agent.run backfill")
    print(f"index: {index.size} labelled questions "
          f"({a.embed_model}, source={a.source})")

    cfg = Config(model=a.model, effort=a.effort, helper_model=a.model,
                 embed_model=a.embed_model, max_steps=a.max_steps,
                 max_tokens=a.max_tokens, phase2=not a.no_phase2)

    done = read_done(full)
    todo = [q for q in store.queue(db, a.tache) if q["f_id"] not in done]
    if a.limit:
        todo = todo[:a.limit]
    print(f"{len(todo)} to do  ({len(done)} already in {full})")

    # the two runtimes share the tools, the schemas and the prompts; only
    # the loop differs. Which one is better is a question for the report,
    # not for the architecture.
    if a.runtime == "langgraph":
        from .graph import build, chat_model
        from .graph import label_question as run_one
        # compiled once, reused for every question - not per question
        extra = {"graph": build(chat_model(cfg))}
    else:
        from .agent import label_question as run_one
        extra = {}

    client = OpenAI()
    themes = store.themes(db, a.tache)
    subject_cache: dict[str, list[dict]] = {}
    rows, spent, t0 = [], 0, time.time()

    for i, q in enumerate(todo, 1):
        if spent > a.budget_tokens:
            print(f"stopping: run token budget reached ({spent:,})")
            break
        if (time.time() - t0) / 60 > a.max_minutes:
            print(f"stopping: {a.max_minutes} minute wall clock reached")
            break
        if q["theme"] not in subject_cache:
            subject_cache[q["theme"]] = store.subjects(db, q["theme"], a.tache)

        row = run_one(q, db, index, client, cfg,
                      subject_cache[q["theme"]], themes, **extra)
        rows.append(row)
        spent += row["total_tokens"]
        append(full, row)
        if row["action"] == "submit_decision":
            append(thin, {"id": q["f_id"], "core_subject": row["agent_core_subject"],
                          "model": a.model})
        flag = ("=" if row["agrees_with_bank"] else
                "~" if row["agrees_with_bank"] is False else " ")
        print(f"  [{i}/{len(todo)}] {flag} f_id {q['f_id']:<6} "
              f"{str(row['agent_core_subject'])[:28]:<30} "
              f"{row['model_calls']}c {row['total_tokens']:>6}t")

    report(rows, f"{a.model} / {a.runtime}", time.time() - t0)
    print(f"\nwrote {full}\n      {thin}")


def cmd_selftest(a) -> None:
    """Everything that can be checked without spending a cent."""
    from .agent import PHASE1, _prompt
    from .tools import TOOLS, TERMINAL

    db = store.session()
    themes = store.themes(db, a.tache)
    assert themes, "no themes in the database"
    print(f"themes ................ {len(themes)}")

    subs = store.subjects(db, themes[0], a.tache)
    print(f"subjects in {themes[0]!r} ... {len(subs)}")

    q = store.queue(db, a.tache, limit=1)
    assert q and q[0]["theme"], "queue empty, or a question has no theme"
    print(f"queue ................. {len(store.queue(db, a.tache))}")

    # every tool schema must be strict-mode legal: additionalProperties false
    # and every property listed in required, or the API rejects the call
    for t in TOOLS:
        p = t["parameters"]
        assert p.get("additionalProperties") is False, t["name"]
        assert set(p.get("properties", {})) == set(p.get("required", [])), \
            f"{t['name']}: required must list every property"
    print(f"tool schemas .......... {len(TOOLS)} ok "
          f"({len(TERMINAL)} terminal)")

    txt = _prompt(q[0], subs, themes)
    assert q[0]["theme"] in PHASE1.replace("<THEME>", q[0]["theme"])
    assert "core_subject" in txt
    print(f"prompt ................ {len(txt)} chars")

    s = breakdown_slots("Je suis votre ami(e). Vous voulez partir en "
                        "vacances. Vous me demandez des idees (budget, "
                        "dates, etc.).")
    assert s["role"] and s["task"] and s["hints"], s
    print(f"slot extraction ....... ok  {s['hints']}")

    idx = store.EmbeddingIndex(a.embed_model, a.source, a.tache)
    idx.load(db)
    print(f"embedding index ....... {idx.size} vectors"
          f"{'  (run backfill)' if idx.size == 0 else ''}")
    print("\nselftest ok - no API call was made")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tache", type=int, default=2)
    p.add_argument("--embed-model", default="text-embedding-3-small")
    p.add_argument("--source", default="text", choices=("text", "abstract"))
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backfill", help="embed the corpus")
    b.add_argument("--limit", type=int)
    b.set_defaults(fn=cmd_backfill)

    r = sub.add_parser("run", help="label questions")
    r.add_argument("-o", "--output", required=True, help="path stem")
    r.add_argument("--limit", type=int)
    r.add_argument("--model", default="gpt-5.6-luna")
    r.add_argument("--effort", default="low",
                   choices=("none", "low", "medium", "high"))
    r.add_argument("--max-steps", type=int, default=8)
    r.add_argument("--max-tokens", type=int, default=60_000,
                   help="per question")
    r.add_argument("--budget-tokens", type=int, default=25_000_000,
                   help="per run")
    r.add_argument("--max-minutes", type=int, default=120)
    r.add_argument("--no-phase2", action="store_true")
    r.add_argument("--runtime", default="plain", choices=("plain", "langgraph"),
                   help="which loop implementation drives the agent")
    r.set_defaults(fn=cmd_run)

    s = sub.add_parser("selftest", help="offline checks, no API call")
    s.set_defaults(fn=cmd_selftest)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
