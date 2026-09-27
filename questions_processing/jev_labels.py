#!/usr/bin/env python3
"""
Theme and core_subject labelling with TypeSafe's Jev, scored against a gold set.

    export TYPESAFE_API_KEY=...

    # the gold test: ask with the gold file's own vocabulary and score against it
    python3 jev_labels.py gold_set/tache2_gold.jsonl --vocab input

    # the same questions, but offered today's 16 themes instead
    python3 jev_labels.py gold_set/tache2_gold.jsonl

    # core_subject, from the database (it needs a theme per question)
    python3 jev_labels.py --from-db --task core_subject --limit 200
    python3 jev_labels.py --from-db --task core_subject --unlabelled -o jev.jsonl

Input
-----
JSONL files in the same shapes llm.py reads and writes, merged by id - so the
questions can come from one file and their labels from another:

    {"id": "...", "text": "..."}                  the questions
    {"id": "...", "theme": "...", "model": "..."}  labels, from anywhere

`text` is required. `theme` is required for --task core_subject, because the
vocabulary is chosen per theme. Any label field present becomes the gold column
and the run is scored; with none, the run just writes labels.

Why this is not a provider in llm.py
------------------------------------
Every provider there speaks one protocol: build a prompt, send a chunk of N
questions, parse a JSON array, check it aligns positionally. Jev takes one
state and returns a typed answer - no chunk, no array, and the misalignment
that machinery guards against cannot happen. Wiring it in would mean faking a
protocol it does not have.

It writes the same {id, <field>, model} JSONL as everything else, so
load_labels.py, compare.py, review.html and the admin upload all accept it.

What differs from the text-LLM version
--------------------------------------
* The vocabulary becomes the answer type, so an out-of-vocabulary label is
  impossible and the unresolved-label loop cannot trigger.
* One question per call. Jev loses accuracy as state fills with unrelated
  material, and there is no batching benefit to weigh against that.
* Every answer carries a calibrated confidence, which is what the report scores
  accuracy against at each threshold.
* There is no few-shot slot, so the worked examples can only go in as prose.
  --no-examples measures whether they still earn their tokens.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DEFAULT_DB = HERE.parent / "backend" / "tcf.db"

# Jev needs a way to say "none of these", or a question whose label is genuinely
# absent from the vocabulary is forced into the nearest option. Same escape
# hatch the text prompt gets from `new_label`.
NONE_OPTION = "(none of these fits)"

# Fields that can hold a gold label, most specific first. `topic` is the older
# name for `theme` and is what the gold set on disk still uses.
LABEL_FIELDS = ("theme", "core_subject", "topic", "gold", "label")


# ----------------------------------------------------------------- input ----

def read_files(paths: list[Path], task: str) -> tuple[list[dict], str | None]:
    """Merge every input file into one row per id.

    Several files because text and labels are written by different steps: the
    scraper emits {id, text, ...} and llm.py emits {id, theme, model}. Later
    files win on a field they set, so a corrected file can follow the output it
    corrects.
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
                row = by_id.setdefault(rid, {"id": rid})
                if rid not in order:
                    order.append(rid)
                for k in ("text", "theme", "core_subject", "topic", "gold", "label"):
                    v = d.get(k)
                    if v not in (None, ""):
                        row[k] = str(v)

    rows = [by_id[i] for i in order if by_id[i].get("text")]
    if len(rows) < len(order):
        print(f"{len(order) - len(rows)} id(s) had no text in any input - skipped",
              file=sys.stderr)

    # the gold column is whatever label field the input carries, preferring the
    # task's own name. `topic` before `theme`-the-input for a core_subject run
    # would be wrong, so the task name is always tried first.
    order_fields = (task,) + tuple(f for f in LABEL_FIELDS if f != task)
    gold_field = next((f for f in order_fields if any(f in r for r in rows)), None)
    if gold_field == "theme" and task == "core_subject":
        gold_field = next((f for f in ("core_subject", "gold", "label")
                           if any(f in r for r in rows)), None)
    return rows, gold_field


def read_db(db: sqlite3.Connection, task: str, tache: int, unlabelled: bool,
            theme: str | None, limit: int | None) -> tuple[list[dict], str | None]:
    col = "t.name" if task == "theme" else "c.name"
    sql = ["select f.id, f.text, t.name, ", col, " from fingerprints f "]
    sql.append("left join themes t on t.id = f.theme_id "
               if task == "theme" else
               "join themes t on t.id = f.theme_id ")
    sql.append("left join core_subjects c on c.id = f.core_subject_id "
               "where f.tache = ?")
    args: list = [tache]
    target = "f.theme_id" if task == "theme" else "f.core_subject_id"
    sql.append(f" and {target} is null" if unlabelled else f" and {target} is not null")
    if theme:
        sql.append(" and t.name = ?")
        args.append(theme)
    sql.append(" order by f.months_seen desc, f.id desc")
    if limit:
        sql.append(f" limit {int(limit)}")
    rows = []
    for i, text, th, label in db.execute("".join(sql), args).fetchall():
        r = {"id": str(i), "text": text}
        if th:
            r["theme"] = th
        if label:
            r[task] = label
        rows.append(r)
    return rows, (None if unlabelled else task)


# ------------------------------------------------------------ vocabulary ----

def theme_options(db: sqlite3.Connection | None, tache: int) -> dict[str, str]:
    """{theme: description} - names from the database, prose from the rules.

    Both halves on purpose. RULES_THEME carries a real description per theme,
    which is exactly what Jev's criteria want, but its headings are prose and
    three of them trail the word "covers". Taking names from the database and
    matching descriptions onto them means a drift between the two shows up as a
    missing description rather than as a silently wrong option.
    """
    from llm_tasks import RULES_THEME

    described: dict[str, str] = {}
    body = RULES_THEME.split("THEME (", 1)[-1]
    for line in body.splitlines():
        m = re.match(r"^([A-Z][^:]{2,45}?)(?:\s+covers)?:\s+(.+)$", line.strip())
        if m:
            described[m.group(1).strip()] = m.group(2).strip()

    if db is None:
        return described
    names = [n for (n,) in db.execute(
        "select name from themes where tache = ? order by name", (tache,))]
    out = {n: described.get(n, n) for n in names}
    missing = [n for n in names if n not in described]
    if missing:
        print(f"no description in RULES_THEME for: {', '.join(missing)}",
              file=sys.stderr)
    return out


def core_subject_options(db: sqlite3.Connection, tache: int) -> dict[str, dict[str, str]]:
    """{theme: {subject: description}} from the database.

    The label is its own description: the vocabulary is already short English
    noun phrases, which is most of the signal. Adding real descriptions is the
    obvious next lever if it underperforms.
    """
    out: dict[str, dict[str, str]] = {}
    for theme, subject in db.execute(
            "select t.name, c.name from core_subjects c "
            "join themes t on t.id = c.theme_id where t.tache = ? "
            "order by t.name, c.name", (tache,)):
        out.setdefault(theme, {})[subject] = subject
    return out


def options_from_rows(rows: list[dict], field: str) -> dict[str, str]:
    """Every distinct label in the input, as the option set.

    This is what makes a gold test honest when the gold predates the current
    vocabulary: tache2_gold.jsonl uses 15 lowercase topics that share not one
    string with today's 16 themes, so scoring it against the current list would
    measure the taxonomy change, not the model.
    """
    labels = sorted({r[field] for r in rows if r.get(field)})
    return {l: l for l in labels}


# ------------------------------------------------------------- the model ----

def instructions(task: str, with_examples: bool) -> str:
    """The labelling policy, reused rather than restated.

    For core_subject only the METHOD half of the rules is used: every line of
    its OUTPUT FORMAT half polices the shape of a free-text answer - noun
    phrase, no articles, reuse the vocabulary - and none of it can be violated
    when the answer is an option from a list.
    """
    from llm_tasks import TASKS

    t = TASKS[task]
    text = t.rules.strip()
    if task == "core_subject":
        text = t.rules.split("OUTPUT FORMAT")[0].strip()
    if with_examples and getattr(t, "examples", None):
        text += "\n\nWorked examples:\n" + "\n".join(
            f"- {x.strip()}\n  -> {y}" for x, y in t.examples)
    return text


def ask(client, Choice, text: str, options: dict[str, str], rules: str):
    """One question, one call. Returns (label_or_None, confidence, model)."""
    reply = client.system_one(
        state=text,
        questions={"label": Choice(
            instructions=rules,
            criteria={**options,
                      NONE_OPTION: "no option above is right for this prompt"})},
    )
    a = reply.answers["label"]
    return (None if a.choice == NONE_OPTION else a.choice,
            float(a.confidence), getattr(reply, "model", "jev"))


# ----------------------------------------------------------------- report ----

def report(results: list[dict], field: str) -> None:
    """Accuracy overall, then accuracy at each confidence floor.

    The second table is the experiment. Two-model agreement buys roughly five
    points of accuracy for twice the calls; if confidence separates right from
    wrong as sharply, it buys the same for one call.
    """
    scored = [r for r in results if r.get("gold")]
    if not scored:
        return
    right = sum(r["label"] == r["gold"] for r in scored)
    declined = sum(r["label"] is None for r in scored)
    print(f"\nscored against `{field}`: {len(scored)} row(s)   "
          f"accuracy {right / len(scored) * 100:.1f}% ({right}/{len(scored)})   "
          f"declined {declined}")

    print(f"\n{'confidence >=':>14} {'kept':>6} {'coverage':>9} {'accuracy':>9}")
    for t in (0.0, 0.5, 0.7, 0.8, 0.85, 0.9, 0.95):
        kept = [r for r in scored if r["confidence"] >= t]
        if not kept:
            continue
        acc = sum(r["label"] == r["gold"] for r in kept) / len(kept)
        print(f"{t:>14.2f} {len(kept):>6} {len(kept) / len(scored) * 100:>8.1f}% "
              f"{acc * 100:>8.1f}%")

    wrong = [r for r in scored if r["label"] != r["gold"]]
    if wrong:
        print("\nmost confident disagreements - what any threshold would let "
              "through, and worth reading as an audit of the gold too:")
        for r in sorted(wrong, key=lambda r: -r["confidence"])[:10]:
            said = "(declined)" if r["label"] is None else repr(r["label"])
            print(f"  {r['confidence']:.2f}  said {said:32} gold {r['gold']!r}")


# ------------------------------------------------------------------ main ----

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="*", type=Path,
                    help="JSONL with id/text, plus any file carrying labels")
    ap.add_argument("--task", choices=("theme", "core_subject"), default="theme")
    ap.add_argument("--from-db", action="store_true",
                    help="read questions from the database instead of files")
    ap.add_argument("--unlabelled", action="store_true",
                    help="--from-db: questions missing this label, to produce new ones")
    ap.add_argument("--vocab", metavar="input|PATH",
                    help="take the options from a file's labels rather than from "
                         "the current vocabulary; `input` means the input files")
    ap.add_argument("--field", help="which label field is the gold (default: detect)")
    ap.add_argument("--tache", type=int, default=2)
    ap.add_argument("--theme", help="one theme only")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--no-examples", action="store_true",
                    help="drop the worked examples from the instructions")
    ap.add_argument("-o", "--output", type=Path)
    args = ap.parse_args()

    if bool(args.input) == bool(args.from_db):
        ap.error("give input files, or --from-db, but not both")
    if not os.environ.get("TYPESAFE_API_KEY"):
        sys.exit("TYPESAFE_API_KEY is not set.\n  export TYPESAFE_API_KEY=...")
    try:
        from typesafe_sdk import Choice, TypeSafeClient
    except ModuleNotFoundError:
        sys.exit("pip install typesafe-sdk")

    db = None
    if args.from_db or args.vocab in (None, "input") or args.task == "core_subject":
        if args.db.exists():
            db = sqlite3.connect(args.db)
        elif args.from_db:
            sys.exit(f"no database at {args.db}")

    # ---- questions ----
    if args.from_db:
        rows, detected = read_db(db, args.task, args.tache, args.unlabelled,
                                 args.theme, args.limit)
    else:
        rows, detected = read_files(args.input, args.task)
        if args.theme:
            rows = [r for r in rows if r.get("theme") == args.theme]
        if args.limit:
            rows = rows[:args.limit]
    gold_field = args.field or detected
    if not rows:
        sys.exit("no questions matched")

    # ---- options ----
    per_theme: dict[str, dict[str, str]] | None = None
    if args.vocab:
        src = rows if args.vocab == "input" else read_files([Path(args.vocab)], args.task)[0]
        field = args.field or next(
            (f for f in (args.task,) + LABEL_FIELDS if any(f in r for r in src)), None)
        if not field:
            sys.exit(f"--vocab {args.vocab}: no label field found "
                     f"(looked for {', '.join(LABEL_FIELDS)})")
        options = options_from_rows(src, field)
        gold_field = gold_field or field
    elif args.task == "theme":
        options = theme_options(db, args.tache)
    else:
        per_theme = core_subject_options(db, args.tache)
        options = {}

    if per_theme is not None:
        missing = {r.get("theme") for r in rows} - set(per_theme)
        if None in missing:
            sys.exit("--task core_subject needs a `theme` on every question; "
                     "the input has rows without one")
        rows = [r for r in rows if r["theme"] in per_theme]
        if missing:
            print(f"skipped theme(s) with no vocabulary: "
                  f"{', '.join(sorted(map(str, missing)))}", file=sys.stderr)

    # A gold column from an older taxonomy scored against today's vocabulary
    # reports near-zero and looks like a model failure. It is not: the two
    # label sets simply do not share strings. Say so rather than printing a
    # number nobody should believe.
    if gold_field:
        offered = set(options) | {k for v in (per_theme or {}).values() for k in v}
        gold_values = {r[gold_field] for r in rows if r.get(gold_field)}
        shared = gold_values & offered
        if gold_values and not shared:
            print(f"WARNING: none of the {len(gold_values)} `{gold_field}` values "
                  f"appear among the {len(offered)} options offered. Scoring this "
                  f"measures the taxonomy change, not the model.\n"
                  f"         Use --vocab input to offer the gold's own labels.",
                  file=sys.stderr)
        elif gold_values and len(shared) < len(gold_values):
            print(f"note: {len(gold_values) - len(shared)} of {len(gold_values)} "
                  f"`{gold_field}` values are not offered as options, so those "
                  f"rows can never be scored correct", file=sys.stderr)

    n_opts = (f"{len(per_theme)} theme vocabularies" if per_theme
              else f"{len(options)} options")
    print(f"{len(rows):,} question(s), {n_opts}, task={args.task}, "
          f"gold={gold_field or 'none'}", file=sys.stderr)

    rules = instructions(args.task, not args.no_examples)
    results: list[dict] = []
    started = time.time()
    with TypeSafeClient() as client:
        for n, r in enumerate(rows, 1):
            opts = per_theme[r["theme"]] if per_theme is not None else options
            try:
                label, conf, model = ask(client, Choice, r["text"], opts, rules)
            except Exception as exc:                        # noqa: BLE001
                print(f"  {r['id']}: failed - {exc}", file=sys.stderr)
                continue
            results.append({"id": r["id"], "label": label, "model": model,
                            "confidence": round(conf, 4),
                            "gold": r.get(gold_field) if gold_field else None})
            if n % 25 == 0 or n == len(rows):
                print(f"  {n}/{len(rows)}  "
                      f"({n / max(time.time() - started, 1e-6):.1f}/s)", file=sys.stderr)

    if gold_field:
        report(results, gold_field)

    if args.output or not gold_field:
        fh = args.output.open("w", encoding="utf-8") if args.output else sys.stdout
        try:
            for r in results:
                # the same shape llm.py writes, under the task's own field name
                json.dump({"id": r["id"], args.task: r["label"],
                           "model": r["model"], "confidence": r["confidence"]},
                          fh, ensure_ascii=False)
                fh.write("\n")
        finally:
            if args.output:
                fh.close()
                print(f"wrote {len(results):,} row(s) to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
