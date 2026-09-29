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
from dataclasses import dataclass, field

from .tools import TERMINAL, TOOLS, ToolBox

PHASE1 = """\
You are labelling questions for a TCF Canada speaking question bank.

Find the most suitable core_subject for this question under theme <THEME>.
Cite the evidence you used.

Rules:
- Prefer an existing core_subject. Only set is_new when none of them fits,
  and then say in rejected_candidates which ones you considered and why each
  one does not fit.
- Search before you answer. evidence_ids must contain f_ids that your tool
  calls actually returned.
- The theme is fixed - do not relabel it. If it looks wrong, say so with
  theme_looks_wrong and suggested_theme, and still give your best subject.
- Deferring is a correct answer when the question is genuinely ambiguous.
  Do not invent a new subject to avoid deferring.
- Finish by calling submit_decision or defer_to_human.
"""

PHASE2 = """\
The question bank currently has this question under core_subject: {current!r}.

That label was produced by a different method and may be right or wrong.
Keep your answer or revise it, and call submit_decision again with your final
position. You may use the tools once more if you need to check something.
"""


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
    listing = "\n".join(f"  {s['name']} ...... {s['uses']}" for s in subjects)
    return (f"Question (f_id {question['f_id']}):\n{question['text']}\n\n"
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
                  question, names, cfg.max_find_similar)
    usage, trace = Usage(), Trace()
    instructions = PHASE1.replace("<THEME>", question["theme"])
    history: list = [{"role": "user",
                      "content": _prompt(question, subjects, all_themes)}]

    name, args = _loop(client, cfg, box, history, usage, trace, instructions)
    blind = dict(args) if args else None
    blind_action = name

    # ---- phase 2: only when there is something independent to compare to ----
    revised = None
    if (cfg.phase2 and name == "submit_decision"
            and question.get("core_subject")
            and usage.total < cfg.max_tokens):
        history.append({"role": "user",
                        "content": PHASE2.format(current=question["core_subject"])})
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
        "action": name,                       # submit_decision | defer | None
        "blind_action": blind_action,
        "blind_core_subject": (blind or {}).get("core_subject"),
        "agent_core_subject": final.get("core_subject"),
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
