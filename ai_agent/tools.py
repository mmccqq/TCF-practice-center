"""The agent's tools, and the JSON schemas the model sees.

Four read tools and two terminal ones. The split that matters is not
read/write - nothing here writes - but *who supplies the argument*:

    find_similar(query, ...)    the MODEL writes the query
    sample_questions(subject)   the MODEL picks the subject

If find_similar only ever took the question's own text there would be exactly
one call the model could make, it would make it on turn 1, and the loop would
be a chain with extra steps. Letting the model rewrite its own query - "library
membership registration" after the raw French returned nothing - is the single
thing that gives the loop a reason to exist.

`list_subjects` is here for OTHER themes only. The working theme's subjects
are pasted into the prompt, because the whole tache-2 vocabulary is 16 themes
and 131 subjects - about 120 tokens. Fetching that with a tool would buy two
round trips per question and nothing else.

The terminal tools end the loop. `submit_decision` absorbs what a separate
propose_subject would have done: reuse and propose are the same act with
`is_new` flipped, and two tools for one meaning would make the model choose a
tool as well as an answer.
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import store

# A cap the model cannot argue with. Without it an uncertain model asks for 50
# neighbours and floods its own context.
MAX_K = 8


# ------------------------------------------------------- question breakdown --

# Tache 2 prompts follow a rigid skeleton:
#   "Je suis un(e) <ROLE>. <SITUATION>. Vous me demandez <TASK> (<HINTS>)."
# Enough of the corpus matches it that the slots are worth extracting with a
# regex rather than a model call: deterministic, free, and identical every run.
_ROLE = re.compile(r"^\s*(?:je\s+suis|je\s+travaille|je\s+viens)\b[^.]*",
                   re.IGNORECASE)
# "vous me demandez/posez ..." is the actual task. "vous voulez ..." is
# usually the candidate's motivation, one sentence earlier - so prefer the
# explicit ask, and fall back to the looser verbs only when there is none.
# 988/1541 questions use "vous me demandez/posez", another 239 the imperative
# "posez-moi". Both are the ask; the weak verbs below are the motivation.
_TASK_STRONG = re.compile(
    r"((?:vous\s+me\s+(?:demandez|posez)|(?:posez|demandez)-moi)[^.]*)",
    re.IGNORECASE)
_TASK_WEAK = re.compile(r"(vous\s+(?:voulez|souhaitez|cherchez)[^.]*)",
                        re.IGNORECASE)
_HINTS = re.compile(r"\(([^)]*)\)")


def breakdown_slots(text: str) -> dict:
    """The deterministic half. Never raises - a slot it cannot find is None,
    because a partial breakdown is still useful and a crash is not."""
    role = _ROLE.search(text)
    strong = _TASK_STRONG.findall(text)
    task = strong[-1] if strong else (_TASK_WEAK.findall(text) or [None])[-1]
    hints = _HINTS.findall(text)
    consumed = {m.strip() for m in
                ([role.group(0)] if role else []) + ([task] if task else [])}
    sentences = [s.strip() for s in re.split(r"(?<=\.)\s+", text) if s.strip()]
    situation = " ".join(s for s in sentences
                         if not any(c[:30] in s for c in consumed))
    return {
        "role": role.group(0).strip() if role else None,
        "task": task.strip() if task else None,
        "hints": [h.strip() for h in hints[-1].split(",")] if hints else [],
        "situation": situation or None,
    }


BREAKDOWN_INSTRUCTIONS = (
    "You are given one TCF Canada speaking prompt and a list of candidate "
    "core_subject labels. Say what the candidate is actually being asked to "
    "talk about, then name the single best label from the list - or the word "
    "NONE if nothing in the list fits. Answer in at most three lines: "
    "TOPIC: ... / LABEL: ... / WHY: ..."
)


def breakdown_blind(text: str, theme: str, subject_names: list[str],
                    client, model: str) -> dict:
    """The blind half: its own model call, with its own clean context.

    It sees the question and the vocabulary. It does NOT see the bank's
    current label or any classifier's suggestion - that is the whole point.
    An anchored breakdown would find features supporting whatever it was
    primed with, and the comparison in phase 2 would confirm rather than test.
    """
    prompt = (f"Theme: {theme}\n"
              f"Candidate labels: {', '.join(subject_names)}\n\n"
              f"Prompt: {text}")
    r = client.responses.create(model=model, instructions=BREAKDOWN_INSTRUCTIONS,
                                input=prompt, reasoning={"effort": "low"})
    return {"opinion": r.output_text.strip(),
            "usage": getattr(r, "usage", None)}


# ------------------------------------------------------------- the tool set --

TOOLS: list[dict[str, Any]] = [
    {"type": "function",
     "name": "question_breakdown",
     "description": ("Parse this question into role / situation / task / "
                     "hints, and get an independent opinion on which "
                     "core_subject fits. Costs one extra model call; worth it "
                     "when the wording is unusual."),
     "parameters": {"type": "object", "properties": {},
                    "required": [], "additionalProperties": False},
     "strict": True},

    {"type": "function",
     "name": "find_similar",
     "description": ("Search the labelled question bank for questions close "
                     "in meaning to a query you write. Returns each "
                     "neighbour's core_subject, theme and similarity score. "
                     "Write a short topical query, not the whole prompt - and "
                     "search again with different words if the first results "
                     "are weak."),
     "parameters": {"type": "object",
                    "properties": {
                        "query": {"type": "string",
                                  "description": "topical phrase to search for"},
                        "k": {"type": "integer",
                              "description": f"how many neighbours, 1-{MAX_K}"}},
                    "required": ["query", "k"],
                    "additionalProperties": False},
     "strict": True},

    {"type": "function",
     "name": "list_subjects",
     "description": ("Every core_subject under a theme, with how many "
                     "questions use each. Use this for themes OTHER than the "
                     "working theme - the working theme's subjects are "
                     "already listed above."),
     "parameters": {"type": "object",
                    "properties": {"theme": {"type": "string"}},
                    "required": ["theme"], "additionalProperties": False},
     "strict": True},

    {"type": "function",
     "name": "sample_questions",
     "description": ("Questions already filed under a core_subject. Use this "
                     "to test a boundary: does this question belong with "
                     "those?"),
     "parameters": {"type": "object",
                    "properties": {
                        "core_subject": {"type": "string"},
                        "n": {"type": "integer"}},
                    "required": ["core_subject", "n"],
                    "additionalProperties": False},
     "strict": True},

    # ---- terminal ----
    {"type": "function",
     "name": "submit_decision",
     "description": ("Your final answer. Call this once you have evidence. "
                     "Set is_new only when no existing subject fits, and then "
                     "list in rejected_candidates the existing subjects you "
                     "considered and why each one does not fit."),
     "parameters": {"type": "object",
                    "properties": {
                        "core_subject": {"type": "string"},
                        "is_new": {"type": "boolean"},
                        "rejected_candidates": {
                            "type": "array",
                            "items": {"type": "object",
                                      "properties": {
                                          "name": {"type": "string"},
                                          "why_not": {"type": "string"}},
                                      "required": ["name", "why_not"],
                                      "additionalProperties": False}},
                        # required, and it is the point: an answer must cite
                        # f_ids it actually retrieved, so answering before
                        # searching is not something the schema can express
                        "evidence_ids": {"type": "array",
                                         "items": {"type": "integer"}},
                        "confidence": {"type": "number"},
                        "reasoning": {"type": "string"},
                        "theme_looks_wrong": {"type": "boolean"},
                        "suggested_theme": {"type": "string"}},
                    "required": ["core_subject", "is_new",
                                 "rejected_candidates", "evidence_ids",
                                 "confidence", "reasoning",
                                 "theme_looks_wrong", "suggested_theme"],
                    "additionalProperties": False},
     "strict": True},

    {"type": "function",
     "name": "defer_to_human",
     "description": ("Use this when the question is genuinely ambiguous or "
                     "the vocabulary has no reasonable home for it. Deferring "
                     "is a correct answer, not a failure - do not invent a "
                     "new subject to avoid it."),
     "parameters": {"type": "object",
                    "properties": {
                        "reason": {"type": "string"},
                        "candidates": {"type": "array",
                                       "items": {"type": "string"}}},
                    "required": ["reason", "candidates"],
                    "additionalProperties": False},
     "strict": True},
]

TERMINAL = {"submit_decision", "defer_to_human"}


# --------------------------------------------------------------- dispatch --

class ToolBox:
    """Binds the tool names to a database session, an index and a budget.

    Errors come back as `{"error": ...}` strings rather than exceptions: an
    error message is a prompt too, and one that names the valid options turns
    a dead run into a self-correcting one.
    """

    def __init__(self, db, index, client, embed_model: str, helper_model: str,
                 question: dict, subject_names: list[str],
                 max_find_similar: int = 4):
        self.db, self.index, self.client = db, index, client
        self.embed_model, self.helper_model = embed_model, helper_model
        self.question, self.subject_names = question, subject_names
        self.max_find_similar = max_find_similar
        self.find_similar_calls = 0
        self.retrieved: set[int] = set()     # what the model is allowed to cite
        self.extra_calls = 0

    def run(self, name: str, args: dict) -> str:
        fn = getattr(self, f"_{name}", None)
        if fn is None:
            return json.dumps({"error": f"no tool named {name!r}"})
        try:
            return json.dumps(fn(**args), ensure_ascii=False)
        except TypeError as e:
            return json.dumps({"error": f"bad arguments for {name}: {e}"})
        except Exception as e:                      # noqa: BLE001
            return json.dumps({"error": f"{type(e).__name__}: {e}"})

    # -- tools --

    def _question_breakdown(self) -> dict:
        slots = breakdown_slots(self.question["text"])
        blind = breakdown_blind(self.question["text"], self.question["theme"],
                                self.subject_names, self.client,
                                self.helper_model)
        self.extra_calls += 1
        return {"slots": slots, "independent_opinion": blind["opinion"]}

    def _find_similar(self, query: str, k: int) -> dict:
        if self.find_similar_calls >= self.max_find_similar:
            return {"error": f"find_similar limit reached "
                             f"({self.max_find_similar} calls). Decide with "
                             f"what you have, or defer_to_human."}
        self.find_similar_calls += 1
        k = max(1, min(int(k), MAX_K))
        vec = store.embed_texts([query], model=self.embed_model)[0]
        # exclude self: re-checking a labelled question would otherwise
        # retrieve itself at score 1.0 and confirm its own label
        hits = self.index.search(self.db, vec, k=k,
                                 exclude={self.question["f_id"]})
        self.retrieved.update(h["f_id"] for h in hits)
        return {"query": query,
                "neighbours": [{"f_id": h["f_id"], "abstract": h["abstract"],
                                "theme": h["theme"],
                                "core_subject": h["core_subject"],
                                "score": h["score"],
                                "text": h["text"][:160]} for h in hits]}

    def _list_subjects(self, theme: str) -> dict:
        rows = store.subjects(self.db, theme)
        if not rows:
            known = ", ".join(store.themes(self.db))
            return {"error": f"unknown theme {theme!r}. Valid themes: {known}"}
        return {"theme": theme, "subjects": rows}

    def _sample_questions(self, core_subject: str, n: int) -> dict:
        rows = store.sample_questions(self.db, core_subject, max(1, min(n, 8)))
        if not rows:
            return {"error": f"no questions are filed under "
                             f"{core_subject!r} yet"}
        self.retrieved.update(r["f_id"] for r in rows)
        return {"core_subject": core_subject,
                "questions": [{"f_id": r["f_id"], "abstract": r["abstract"],
                               "text": r["text"][:160]} for r in rows]}
