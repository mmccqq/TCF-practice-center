"""The labelling agent: two phases, one loop each.

    PHASE 1  blind.  The agent is told the question and the theme's
             vocabulary, and nothing about what anyone else thinks the answer
             is. It routes its own tools and commits.

    PHASE 2  informed.  It is shown the bank's current label and asked to keep
             or revise.

Why phase 1 is blind
--------------------
If the agent sees the existing label first, everything downstream is read in
its light: the breakdown finds features that support it, the neighbours that
agree stand out, and the "comparison" in phase 2 confirms rather than tests.
Two views only mean something when they were formed independently - the same
reason two models agreeing scored 90-92% against 85-86% for one. Phase 1
being blind is what makes the phase-2 agreement a signal instead of an echo.

Stopping
--------
Three stop conditions, deliberately:

    submit_decision / defer_to_human   the model is done
    MAX_STEPS                          the model cannot influence this
    the token budget                   nor this

The first is a *tool call*, not "no tool call" - which is what lets the answer
carry a schema, a confidence and, above all, `evidence_ids`. A model cannot
fill in ids it never retrieved, so "search before answering" stops being an
instruction it may ignore and becomes a field it cannot fabricate.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .tools import TERMINAL, TOOLS, ToolBox, breakdown_slots

def _method() -> str:
    """The METHOD half of RULES_CORE_SUBJECT - the same reading instructions
    llm.py and jev_labels.py give their models. Taken from llm_tasks rather
    than restated, so the three labellers cannot drift apart. Its steps are
    re-lettered, because they sit inside step 1 below."""
    from .store import llm_tasks
    rules = llm_tasks().RULES_CORE_SUBJECT
    body = rules.split("METHOD", 1)[1].split("OUTPUT FORMAT", 1)[0].strip()
    body = re.sub(r"^(\d)\.", lambda m: f"({'abcdefgh'[int(m.group(1)) - 1]})",
                  body, flags=re.M)
    return "\n".join("   " + line for line in body.splitlines())


# The first gold run showed the agent reasoning from precedent: "the retrieved
# near-duplicates are filed under 'films'", and the bank was wrong. So the
# order of authority is spelled out: the question, then the notes, then the
# bank - and the bank never overrides the other two.
PHASE1 = """\
You are labelling questions for a TCF Canada speaking question bank.

Find the most suitable core_subject for this question under theme <THEME>.

HOW TO DECIDE, in this order:
1. Analyse the question itself.
""" + _method() + """
   The parenthetical hints at the end of the prompt are the strongest signal
   for telling similar subjects apart. The request clause and the hints are
   extracted for you under the question.
2. Apply the notes. Where the subject list carries a note, that note is the
   rule for telling those subjects apart, and it decides.
3. Check the bank. Use find_similar and sample_questions to see which labels
   are in use and to stay consistent. The bank's labels can be outdated or
   wrong - especially on the boundaries the notes describe - so a precedent
   never overrides a note or what the question plainly says. A label that is
   not in the subject list is not a valid answer, however many examples
   carry it.

Rules:
- Fill request_clause, decisive_features and rule_applied from your own
  reading of the question, before choosing core_subject.
- Prefer an existing core_subject. Only set is_new when none of them fits,
  and then say in rejected_candidates which ones you considered and why each
  one does not fit.
- evidence_ids must contain f_ids your tool calls actually returned - cite
  the examples you checked, including ones you decided against.
- The theme is fixed - do not relabel it. If it looks wrong, say so with
  theme_looks_wrong and suggested_theme, and still give your best subject.
- Deferring is a correct answer when the question is genuinely ambiguous.
  Do not invent a new subject to avoid deferring.
- Finish by calling submit_decision or defer_to_human.
"""

PHASE2 = """\
{who}: {current!r}.

That label was produced by a different method and may be right or wrong.
Keep your answer or revise it, and call submit_decision again with your final
position. You may use the tools once more if you need to check something.
"""


def phase2_message(question: dict) -> tuple[str, str] | tuple[None, None]:
    """(message, what it compares against) for phase 2, or (None, None).

    A classifier's suggestion wins over the bank's label when both exist: the
    questions routed here are the ones the classifier was unsure about, and
    whether the agent agrees with it is the thing being measured. The bank's
    label is the comparison when re-checking questions already labelled.
    """
    if question.get("phase2_label"):
        return (PHASE2.format(who="A classifier suggested this core_subject",
                              current=question["phase2_label"]),
                question.get("phase2_source") or "classifier")
    if question.get("core_subject"):
        return (PHASE2.format(who="The question bank currently has this "
                                  "question under core_subject",
                              current=question["core_subject"]), "bank")
    return None, None


@dataclass
class Config:
    model: str = "gpt-5.6-luna"
    effort: str = "low"
    helper_model: str = "gpt-5.6-luna"          # the blind breakdown call
    embed_model: str = "text-embedding-3-small"
    max_steps: int = 8
    max_tokens: int = 60_000                    # per question, both phases
    max_find_similar: int = 4
    phase2: bool = True
    vocab: str = "vocab"                        # "vocab" llm_tasks | "db" tables


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, resp) -> None:
        u = getattr(resp, "usage", None)
        self.calls += 1
        if u is not None:
            self.input_tokens += getattr(u, "input_tokens", 0) or 0
            self.output_tokens += getattr(u, "output_tokens", 0) or 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class Trace:
    steps: list[str] = field(default_factory=list)

    def note(self, s: str) -> None:
        self.steps.append(s)


def _prompt(question: dict, subjects: list[dict], all_themes: list[str]) -> str:
    listing = "\n".join(f"  {s['name']} ...... {s['uses']}"
                        + (f"\n      {s['note']}" if s.get("note") else "")
                        for s in subjects)
    # The regex slots, pasted rather than offered as a tool: question_breakdown
    # was called 0 times in 32 questions, and these cost nothing. The hints
    # are the strongest signal there is, so they go in front of the model.
    slots = breakdown_slots(question["text"])
    extracted = ""
    if slots.get("task"):
        extracted += f"Request clause: {slots['task'].split('(')[0].strip()}\n"
    if slots.get("hints"):
        extracted += f"Hints: {', '.join(h for h in slots['hints'] if h != 'etc.')}\n"
    # no f_id here: shown its own id, the model cites the question as evidence
    # for itself (4 of 32 did, in the first gold run)
    return (f"Question:\n{question['text']}\n"
            f"{extracted}\n"
            f"Theme: {question['theme']}\n\n"
            f"Existing core_subjects under this theme (name ...... uses):\n"
            f"{listing or '  (none yet)'}\n\n"
            f"All themes, for reference only - do not relabel the theme:\n"
            f"  {', '.join(all_themes)}\n")


def _loop(client, cfg: Config, box: ToolBox, history: list, usage: Usage,
          trace: Trace, instructions: str) -> tuple[str, dict] | tuple[None, None]:
    """Turn the loop until a terminal tool fires, or a cap does."""
    nudged = False
    for step in range(cfg.max_steps):
        if usage.total > cfg.max_tokens:
            trace.note(f"stop: token budget ({usage.total})")
            return None, None
        resp = client.responses.create(
            model=cfg.model, instructions=instructions, tools=TOOLS,
            input=history, reasoning={"effort": cfg.effort})
        usage.add(resp)
        # keep every output item, reasoning included: dropping it makes the
        # model re-derive its plan from scratch on every turn
        history += resp.output

        calls = [i for i in resp.output if i.type == "function_call"]
        if not calls:
            # no tool call and no terminal call: the model answered in prose.
            # Push it back once rather than accepting an unschema'd answer.
            trace.note("prose answer, re-prompting for a tool call")
            history.append({"role": "user", "content":
                            "Answer by calling submit_decision or "
                            "defer_to_human."})
            continue

        for call in calls:
            args = json.loads(call.arguments or "{}")
            if call.name in TERMINAL:
                # The one intervention. `strict` mode can force the field to
                # be PRESENT but not to be non-empty, so an unevidenced answer
                # is still expressible - and an empty evidence_ids is the
                # under-searching failure wearing a valid schema. Worth
                # exactly one push-back, then accept whatever comes back.
                if (call.name == "submit_decision" and not nudged
                        and (not box.retrieved or not args.get("evidence_ids"))):
                    nudged = True
                    why = ("You have not retrieved anything yet"
                           if not box.retrieved else
                           "evidence_ids is empty")
                    trace.note(f"pushed back: {why}")
                    history.append({"type": "function_call_output",
                                    "call_id": call.call_id,
                                    "output": json.dumps({
                                        "error": f"{why}, so this decision "
                                        "cites no evidence. Use find_similar "
                                        "or sample_questions, then submit "
                                        "again with the f_ids you actually "
                                        "saw."})})
                    continue
                trace.note(f"{call.name}")
                return call.name, args

            out = box.run(call.name, args)
            trace.note(f"{call.name}({json.dumps(args, ensure_ascii=False)[:70]})")
            history.append({"type": "function_call_output",
                            "call_id": call.call_id, "output": out})

    trace.note(f"stop: MAX_STEPS ({cfg.max_steps})")
    return None, None


def label_question(question: dict, db, index, client, cfg: Config,
                   subjects: list[dict], all_themes: list[str]) -> dict:
    """One question, both phases. Returns a report row - never writes."""
    names = [s["name"] for s in subjects]
    box = ToolBox(db, index, client, cfg.embed_model, cfg.helper_model,
                  question, names, cfg.max_find_similar, cfg.vocab)
    usage, trace = Usage(), Trace()
    instructions = PHASE1.replace("<THEME>", question["theme"])
    history: list = [{"role": "user",
                      "content": _prompt(question, subjects, all_themes)}]

    name, args = _loop(client, cfg, box, history, usage, trace, instructions)
    blind = dict(args) if args else None
    blind_action = name

    # ---- phase 2: only when there is something independent to compare to ----
    revised = None
    p2_msg, p2_against = phase2_message(question) if cfg.phase2 else (None, None)
    if (p2_msg and name == "submit_decision"
            and usage.total < cfg.max_tokens):
        history.append({"role": "user", "content": p2_msg})
        n2, a2 = _loop(client, cfg, box, history, usage, trace, instructions)
        if n2 == "submit_decision":
            revised, name, args = dict(a2), n2, a2
        elif n2 == "defer_to_human":
            revised, name, args = dict(a2), n2, a2

    final = dict(args) if args else {}
    cited = set(final.get("evidence_ids") or [])
    return {
        "f_id": question["f_id"],
        "theme": question["theme"],
        "current_core_subject": question.get("core_subject"),
        "phase2_against": p2_against,
        "action": name,                       # submit_decision | defer | None
        "blind_action": blind_action,
        "blind_core_subject": (blind or {}).get("core_subject"),
        "agent_core_subject": final.get("core_subject"),
        "request_clause": final.get("request_clause"),
        "decisive_features": final.get("decisive_features"),
        "rule_applied": final.get("rule_applied"),
        "is_new": final.get("is_new"),
        "confidence": final.get("confidence"),
        "reasoning": final.get("reasoning"),
        "rejected_candidates": final.get("rejected_candidates"),
        "evidence_ids": sorted(cited),
        # a cited id the tools never returned is a fabricated citation - the
        # thing required-evidence is meant to prevent, so it is recorded
        "evidence_ok": bool(cited) and cited <= box.retrieved,
        "uncited_ids": sorted(cited - box.retrieved),
        "theme_looks_wrong": final.get("theme_looks_wrong"),
        "suggested_theme": final.get("suggested_theme") or None,
        "defer_reason": final.get("reason") if name == "defer_to_human" else None,
        "agrees_with_bank": (
            None if not question.get("core_subject")
            or name != "submit_decision"
            else final.get("core_subject") == question["core_subject"]),
        "revised_in_phase2": (
            None if revised is None or blind is None
            else revised.get("core_subject") != blind.get("core_subject")),
        "steps": trace.steps,
        "model_calls": usage.calls + box.extra_calls,
        "find_similar_calls": box.find_similar_calls,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total,
    }
