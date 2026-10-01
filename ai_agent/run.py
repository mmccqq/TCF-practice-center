#!/usr/bin/env python3
"""Run the labelling agent over the question bank. Local, report-only.

    export OPENAI_API_KEY=...

    # 1. one-time: embed the corpus (about 1,541 tache-2 questions)
    python3 -m ai_agent.run backfill

    # 2. the smoke run - 20 questions, read every trace by hand
    python3 -m ai_agent.run run --limit 20 -o out/smoke

    # 3. the full run
    python3 -m ai_agent.run run -o out/run1

    # the same agent on the LangGraph runtime - same tools, same prompts
    python3 -m ai_agent.run run --limit 20 --runtime langgraph -o out/lg

    # the routing: only what Jev was unsure about, scored against a gold file.
    # Phase 2 compares with Jev's answer; the gold is never shown to the agent.
    python3 -m ai_agent.run run \
        --jev questions_processing/gold_set/tache2_gold_jev.jsonl --below 0.8 \
        --gold questions_processing/gold_set/tache2_gold.jsonl -o out/gold_low

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

QUEUE
    Default: every tache-2 question with a theme, labelled or not, from the
    database. With --jev FILE: only rows of a jev_labels.py core_subject output
    whose confidence is below --below (default 0.8) - the routing half of the
    two-stage design. --gold FILE then supplies each question's text and theme
    (the same ones Jev was given) and the label it is scored against.

VOCABULARY
    --vocab vocab (default) offers llm_tasks.VOCABULARY, the same options
    jev_labels.py offers, so the two are comparable. --vocab db offers the
    themes / core_subjects tables instead. Either way the evidence -
    find_similar's neighbours, sample_questions - is the bank's labels as
    they stand.

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

def _key(row: dict):
    """What "already done" is keyed on. The source id when there is one: the
    same fingerprint can arrive twice under two months' ids - and a gold file
    can label the two differently - so the fingerprint alone would skip one."""
    return row.get("source_id") or row["f_id"]


def read_done(path: Path) -> set:
    if not path.exists():
        return set()
    done = set()
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                done.add(_key(json.loads(line)))
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

    scored = [r for r in rows if "correct" in r]
    if scored:
        a_ok = sum(r["correct"] for r in scored)
        j_ok = sum(r["jev_correct"] for r in scored)
        print(f"\n  against gold ({len(scored)} rows)")
        print(f"    agent correct       {a_ok:>4} / {len(scored)}   {a_ok/len(scored)*100:.1f}%")
        print(f"    jev correct         {j_ok:>4} / {len(scored)}   {j_ok/len(scored)*100:.1f}%"
              f"   <- the baseline on the same rows")
        fixed = sum(r["correct"] and not r["jev_correct"] for r in scored)
        broke = sum(r["jev_correct"] and not r["correct"] for r in scored)
        print(f"    agent fixed a jev miss   {fixed:>3}")
        print(f"    agent broke a jev hit    {broke:>3}")
        agreed = [r for r in scored if r["agent_core_subject"] == r["jev_core_subject"]]
        if agreed:
            ok = sum(r["correct"] for r in agreed)
            print(f"    when agent == jev: {ok}/{len(agreed)} correct"
                  f"   (the independence signal)")

    # what any review should start with: the disagreements, most confident
    # first - the same thing jev_labels.py's report prints, for the same reason
    wrong = [r for r in checked if r["agrees_with_bank"] is False]
    if wrong:
        print(f"\n  most confident disagreements with the bank:")
        for r in sorted(wrong, key=lambda r: -(r["confidence"] or 0))[:10]:
            print(f"    {r['confidence']:.2f}  f_id {r['f_id']:<6} "
                  f"bank {r['current_core_subject']!r:32} "
                  f"agent {r['agent_core_subject']!r}")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"not found: {path}")
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def jev_queue(db, jev: Path, below: float, gold: Path | None) -> list[dict]:
    """The rows Jev was unsure about, as agent questions.

    Each question carries Jev's answer as the phase-2 comparison and, when a
    gold file is given, the gold label - which the agent never sees; run.py
    scores against it after the fact.
    """
    rows = read_jsonl(jev)
    if not rows or "core_subject" not in rows[0]:
        field = next((k for k in (rows[0] if rows else {})
                      if k not in ("id", "model", "confidence")), "?")
        sys.exit(f"{jev} is a `{field}` run; the agent labels core_subject")
    low = [r for r in rows if float(r.get("confidence", 0)) < below]
    golds = {str(g["id"]): g for g in read_jsonl(gold)} if gold else {}
    f_ids = store.resolve_ids(db, [str(r["id"]) for r in low])

    out, unresolved, no_theme = [], [], []
    for r in low:
        sid = str(r["id"])
        f_id = f_ids.get(sid)
        q = store.question(db, f_id) if f_id else None
        if q is None:
            unresolved.append(sid)
            continue
        g = golds.get(sid)
        q = {**q,
             "source_id": sid,
             "bank_core_subject": q["core_subject"],
             # blank the bank label: for these rows the comparison is Jev,
             # and "agrees with the bank" would be noise in the report
             "core_subject": None,
             "phase2_label": r.get("core_subject"),
             "phase2_source": "jev",
             "jev_core_subject": r.get("core_subject"),
             "jev_confidence": float(r.get("confidence", 0)),
             "gold_core_subject": g.get("core_subject") if g else None}
        if g:
            # the same text and theme Jev was given, so the two are compared
            # on the same input
            q["text"] = g.get("text") or q["text"]
            q["theme"] = g.get("theme") or q["theme"]
        if not q["theme"]:
            no_theme.append(sid)
            continue
        out.append(q)
    print(f"{len(low)} of {len(rows)} Jev rows below {below}"
          + (f"; {len(unresolved)} not in the database" if unresolved else "")
          + (f"; {len(no_theme)} with no theme" if no_theme else ""))
    return out


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
    done = read_done(full)
    queue = (jev_queue(db, Path(a.jev), a.below, Path(a.gold) if a.gold else None)
             if a.jev else store.queue(db, a.tache))
    todo = [q for q in queue if _key(q) not in done]
    if a.limit:
        todo = todo[:a.limit]
    print(f"{len(todo)} to do  ({len(done)} already in {full})")

    if a.vocab == "vocab":
        themes, subjects_for = store.vocab_themes(), store.vocab_subjects
    else:
        themes, subjects_for = store.themes(db, a.tache), store.subjects
    off_vocab = sorted({q["theme"] for q in todo} - set(themes))
    if off_vocab:
        print(f"  ! theme(s) not in the {a.vocab} vocabulary, so those rows get "
              f"no options: {', '.join(off_vocab)}")

    if a.dry_run:
        for q in todo:
            print(f"  f_id {q['f_id']:<6} {q.get('source_id', ''):<14} "
                  f"{q['theme'][:26]:<27} jev {str(q.get('jev_core_subject'))[:24]:<25}"
                  f"{q.get('jev_confidence', ''):<6} gold {q.get('gold_core_subject')}")
        print("\ndry run - no API call made")
        return

    index = store.EmbeddingIndex(a.embed_model, a.source, a.tache)
    index.load(db)
    if index.size == 0:
        sys.exit(f"no embeddings for ({a.embed_model}, {a.source}). "
                 f"Run:  python3 -m ai_agent.run backfill")
    print(f"index: {index.size} labelled questions "
          f"({a.embed_model}, source={a.source})")

    cfg = Config(model=a.model, effort=a.effort, helper_model=a.model,
                 embed_model=a.embed_model, max_steps=a.max_steps,
                 max_tokens=a.max_tokens, phase2=not a.no_phase2,
                 vocab=a.vocab)

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
            subject_cache[q["theme"]] = subjects_for(db, q["theme"], a.tache)

        row = run_one(q, db, index, client, cfg,
                      subject_cache[q["theme"]], themes, **extra)
        for k in ("source_id", "bank_core_subject", "jev_core_subject",
                  "jev_confidence", "gold_core_subject"):
            if k in q:
                row[k] = q[k]
        if q.get("gold_core_subject"):
            row["correct"] = row["agent_core_subject"] == q["gold_core_subject"]
            row["jev_correct"] = q.get("jev_core_subject") == q["gold_core_subject"]
        rows.append(row)
        spent += row["total_tokens"]
        append(full, row)
        if row["action"] == "submit_decision":
            append(thin, {"id": q.get("source_id") or q["f_id"],
                          "core_subject": row["agent_core_subject"],
                          "model": a.model})
        if "correct" in row:
            flag = "✓" if row["correct"] else "✗"
        else:
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
    r.add_argument("--jev", help="queue = this jev_labels.py core_subject "
                                 "output, below --below")
    r.add_argument("--below", type=float, default=0.8,
                   help="with --jev: confidence under this goes to the agent")
    r.add_argument("--gold", help="with --jev: gold JSONL for text, theme "
                                  "and scoring")
    r.add_argument("--vocab", default="vocab", choices=("vocab", "db"),
                   help="options from llm_tasks.VOCABULARY or the tables")
    r.add_argument("--dry-run", action="store_true",
                   help="build and print the queue; no API call")
    r.set_defaults(fn=cmd_run)

    s = sub.add_parser("selftest", help="offline checks, no API call")
    s.set_defaults(fn=cmd_selftest)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
