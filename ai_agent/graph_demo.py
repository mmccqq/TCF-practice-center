#!/usr/bin/env python3
"""Watch the LangGraph agent run, one node at a time. No API calls.

    .venv/bin/python -m ai_agent.graph_demo

What is real and what is scripted:

    real       the graph from graph.py, its nodes and routers, the ToolBox,
               the database, sample_questions, the prompt, the notes
    scripted   the model's replies (ScriptedChat), so the run is free and
               the same every time; and find_similar's neighbours, which
               are real questions from the bank but chosen here, because a
               real search would need the embeddings API

The question is a real one from the gold set - a colleague who has just seen
a film at the cinema, hints (histoire, acteurs, horaires). Gold says
"films in cinema". The script has the agent make the classic mistake first
("films"), then correct itself in phase 2 by applying the note - so one run
passes through every node and both routers.

graph.stream(..., stream_mode="updates") yields {node_name: what_it_returned}
after each step. That is exactly LangGraph's view of the run: which node ran,
and which changes it handed back to be merged into the state.
"""
from __future__ import annotations

import json
import sys
import textwrap

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from . import graph as lg
from . import store
from .agent import Config
from .test_agent import ScriptedChat, call

QUESTION_ID = 1865          # 0320260421002 in the gold set
NEIGHBOURS = [(429, 0.91), (2051, 0.88)]  # real film questions, filed as "films"

ANALYSIS = dict(request_clause="", decisive_features="", rule_applied="none",
                is_new=False, rejected_candidates=[], confidence=0.8,
                reasoning="", theme_looks_wrong=False, suggested_theme="")

# The model's replies, one list per turn. [] = a reply with no tool call.
SCRIPT = [
    [call("find_similar", {"query": "avis sur un film vu au cinéma", "k": 2}, "c1")],
    [],                                                              # prose
    [call("submit_decision", {**ANALYSIS, "core_subject": "films",
                              "evidence_ids": []}, "c2")],           # no evidence
    [call("sample_questions", {"core_subject": "films in cinema", "n": 3}, "c3")],
    [call("submit_decision", {**ANALYSIS, "core_subject": "films",
                              "decisive_features": "a film the colleague saw",
                              "evidence_ids": [429, 2051]}, "c4")],  # blind answer
    [call("submit_decision", {**ANALYSIS, "core_subject": "films in cinema",
                              "decisive_features": "cinema named; hints ask for horaires",
                              "rule_applied": "films in cinema",
                              "confidence": 0.9,
                              "evidence_ids": [429]}, "c5")],        # phase 2
]


class BankNeighbours:
    """Stands in for EmbeddingIndex: returns real bank rows, picked by hand."""
    size = len(NEIGHBOURS)

    def __init__(self, db):
        self.rows = [{**store.question(db, f), "score": s} for f, s in NEIGHBOURS]

    def load(self, db):
        pass

    def search(self, db, vec, k=5, exclude=()):
        return [r for r in self.rows if r["f_id"] not in set(exclude)][:k]


def short(text: str, n: int = 90) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + "…"


def describe(update: dict) -> list[str]:
    """One node's returned changes, in plain lines."""
    lines = []
    for key, value in update.items():
        if key == "messages":
            for m in value:
                if isinstance(m, AIMessage):
                    if m.tool_calls:
                        for c in m.tool_calls:
                            args = {k: v for k, v in c["args"].items()
                                    if k in ("query", "k", "core_subject", "n",
                                             "evidence_ids", "rule_applied")}
                            lines.append(f"messages += AI: call {c['name']}{json.dumps(args, ensure_ascii=False)}")
                    else:
                        lines.append("messages += AI: (text only, no tool call)")
                elif isinstance(m, ToolMessage):
                    lines.append(f"messages += Tool: {short(m.content)}")
                elif isinstance(m, HumanMessage):
                    lines.append(f"messages += Human: {short(m.content)}")
        elif key == "steps":
            lines.append(f"steps     = {value}")
        elif key in ("blind", "final"):
            lines.append(f"{key:<9} = core_subject {value.get('core_subject')!r}")
        else:
            lines.append(f"{key:<9} = {value!r}")
    return lines


def main() -> None:
    store.embed_texts = lambda texts, model=None: [[0.1] * 8 for _ in texts]
    db = store.session()
    q = store.question(db, QUESTION_ID)
    if q is None:
        sys.exit(f"question {QUESTION_ID} is not in this database")
    q = {**q, "core_subject": None, "phase2_label": "films in cinema",
         "phase2_source": "jev"}

    model = ScriptedChat(script=[list(t) for t in SCRIPT])
    graph = lg.build(model, max_tokens=60_000)
    cfg = Config()
    subjects = store.vocab_subjects(db, q["theme"])

    print("QUESTION", QUESTION_ID)
    print(textwrap.fill(q["text"], 88, initial_indent="  ", subsequent_indent="  "))
    print(f"  theme: {q['theme']}    Jev (phase 2): films in cinema\n")

    # label_question builds this same init; it is spelled out here to show it
    p2_msg, _ = lg.phase2_message(q)
    from .tools import ToolBox
    box = ToolBox(db, BankNeighbours(db), None, cfg.embed_model, cfg.helper_model,
                  q, [s["name"] for s in subjects], cfg.max_find_similar, cfg.vocab)
    init = {"messages": [HumanMessage(lg._prompt(q, subjects, store.vocab_themes()))],
            "box": box, "phase": 1, "blind": None, "final": None, "action": None,
            "nudged": False, "steps": [], "phase2_msg": p2_msg, "halted": False,
            "instructions": lg.PHASE1.replace("<THEME>", q["theme"])}

    updates = list(graph.stream(init, {"recursion_limit": cfg.max_steps * 3},
                                stream_mode="updates"))
    nodes = [next(iter(u)) for u in updates]

    for i, (u, node) in enumerate(zip(updates, nodes), 1):
        nxt = nodes[i] if i < len(nodes) else "END"
        how = ("route_agent" if node == "agent" else
               "route_gate" if node == "gate" else "fixed edge")
        print(f"step {i:>2}  {node.upper()}")
        for line in describe(u[node]):
            print(f"          {line}")
        print(f"          {how} → {nxt}\n")

    print(f"{len(updates)} node runs · {sum(n == 'agent' for n in nodes)} model calls")


if __name__ == "__main__":
    main()
