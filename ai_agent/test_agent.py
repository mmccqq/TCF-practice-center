"""Loop behaviour for BOTH runtimes, driven by scripted fake models.

    .venv/bin/python -m pytest ai_agent/test_agent.py -q

Every test runs twice - once against the hand-written loop in agent.py, once
against the LangGraph version in graph.py - because "the same agent, a
different runtime" is a claim worth enforcing rather than asserting. If the
two ever diverge, this is where it shows up.

The five cases are the ones that are easy to get wrong and invisible in a
successful run: the evidence push-back, fabricated citations, the step cap,
phase-2 revision, and defer. No API call, no cost.
"""
from __future__ import annotations

import json
import types

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from . import agent as plain
from . import graph as lg
from . import store
from .agent import Config

D = dict(request_clause="rc", decisive_features="df", rule_applied="none",
         is_new=False, rejected_candidates=[], confidence=0.8, reasoning="r",
         theme_looks_wrong=False, suggested_theme="")


# ---------------------------------------------------------- fake runtimes --

def call(name, args, cid):
    """One tool call, in both shapes: the Responses item agent.py reads and
    the LangChain dict graph.py reads."""
    return {"name": name, "args": args, "id": cid}


class FakeResp:
    usage = types.SimpleNamespace(input_tokens=900, output_tokens=200)

    def __init__(self, calls):
        self.output = [types.SimpleNamespace(
            type="function_call", name=c["name"],
            arguments=json.dumps(c["args"]), call_id=c["id"]) for c in calls]
        self.output_text = ""


class FakeClient:
    """Stands in for `openai.OpenAI` for agent.py."""

    def __init__(self, script):
        self.script = [list(turn) for turn in script]

    @property
    def responses(self):
        return self

    def create(self, **_):
        return FakeResp(self.script.pop(0))


class ScriptedChat(BaseChatModel):
    """Stands in for `ChatOpenAI` for graph.py."""

    script: list = []

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        turn = self.script.pop(0)
        msg = AIMessage(content="", tool_calls=list(turn),
                        usage_metadata={"input_tokens": 900,
                                        "output_tokens": 200,
                                        "total_tokens": 1100})
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools, **kw):
        return self

    @property
    def _llm_type(self) -> str:
        return "scripted"


class FakeIndex:
    size = 2

    def load(self, db):
        pass

    def search(self, db, vec, k=5, exclude=()):
        rows = [{"f_id": 111, "abstract": "library membership", "text": "x",
                 "theme": "Daily life", "core_subject": "public services",
                 "score": 0.91},
                {"f_id": 222, "abstract": "gym signup", "text": "x",
                 "theme": "Daily life", "core_subject": "public services",
                 "score": 0.44}]
        return [r for r in rows if r["f_id"] not in set(exclude)][:k]


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setattr(store, "embed_texts",
                        lambda texts, model=None: [[0.1] * 8 for _ in texts])
    db = store.session()
    q = store.queue(db, limit=1)
    if not q:
        pytest.skip("no labelled questions in this database")
    question = dict(q[0])
    question["core_subject"] = "lessons"
    return db, question, store.subjects(db, question["theme"]), store.themes(db)


@pytest.fixture(params=["plain", "langgraph"])
def run(request, ctx):
    db, q, subs, themes = ctx

    def go(script, question=None, **cfg):
        q_ = question or q
        c = Config(**cfg)
        if request.param == "plain":
            return plain.label_question(q_, db, FakeIndex(),
                                        FakeClient(script), c, subs, themes)
        return lg.label_question(q_, db, FakeIndex(), FakeClient([]), c,
                                 subs, themes,
                                 model=ScriptedChat(script=[list(t)
                                                            for t in script]))
    go.runtime = request.param
    return go


# -------------------------------------------------------------- the cases --

def test_empty_evidence_is_pushed_back_once(run):
    """strict mode can require the FIELD but not a non-empty list, so an
    unevidenced answer is still expressible. It gets exactly one push-back."""
    r = run([
        [call("find_similar", {"query": "music school", "k": 2}, "1")],
        [call("submit_decision", {**D, "core_subject": "lessons",
                                  "evidence_ids": []}, "2")],
        [call("submit_decision", {**D, "core_subject": "lessons",
                                  "evidence_ids": [111]}, "3")],
        [call("submit_decision", {**D, "core_subject": "lessons",
                                  "evidence_ids": [111, 222]}, "4")],
    ])
    assert any("pushed back" in s for s in r["steps"])
    assert r["evidence_ok"] is True
    assert r["agrees_with_bank"] is True


def test_fabricated_citation_is_recorded(run):
    r = run([
        [call("find_similar", {"query": "x", "k": 1}, "1")],
        [call("submit_decision", {**D, "core_subject": "lessons",
                                  "evidence_ids": [999]}, "2")],
        [call("submit_decision", {**D, "core_subject": "lessons",
                                  "evidence_ids": [999]}, "3")],
    ])
    assert r["evidence_ok"] is False
    assert r["uncited_ids"] == [999]


def test_a_model_that_never_submits_is_stopped(run):
    r = run([[call("find_similar", {"query": "x", "k": 1}, f"z{i}")]
             for i in range(40)], max_steps=3)
    assert r["action"] is None
    assert "stop:" in r["steps"][-1]
    assert r["find_similar_calls"] <= Config().max_find_similar


def test_phase2_revision_is_detected(run):
    r = run([
        [call("find_similar", {"query": "x", "k": 2}, "1")],
        [call("submit_decision", {**D, "core_subject": "community lessons",
                                  "evidence_ids": [111]}, "2")],
        [call("submit_decision", {**D, "core_subject": "university course",
                                  "evidence_ids": [111]}, "3")],
    ])
    assert r["blind_core_subject"] == "community lessons"
    assert r["agent_core_subject"] == "university course"
    assert r["revised_in_phase2"] is True
    assert r["agrees_with_bank"] is False


def test_defer_is_terminal(run):
    r = run([
        [call("find_similar", {"query": "x", "k": 1}, "1")],
        [call("defer_to_human", {"reason": "ambiguous",
                                 "candidates": ["lessons"]}, "2")],
    ])
    assert r["action"] == "defer_to_human"
    assert r["agent_core_subject"] is None
    assert r["defer_reason"] == "ambiguous"


def test_phase2_compares_against_the_classifier_when_given(run, ctx):
    """Routed rows carry Jev's answer and no bank label: phase 2 must still
    happen, against Jev, and "agrees with the bank" must stay None."""
    _, q, _, _ = ctx
    routed = {**q, "core_subject": None, "phase2_label": "community lessons",
              "phase2_source": "jev"}
    r = run([
        [call("find_similar", {"query": "x", "k": 2}, "1")],
        [call("submit_decision", {**D, "core_subject": "lessons",
                                  "evidence_ids": [111]}, "2")],
        [call("submit_decision", {**D, "core_subject": "community lessons",
                                  "evidence_ids": [111]}, "3")],
    ], question=routed)
    assert r["phase2_against"] == "jev"
    assert r["blind_core_subject"] == "lessons"
    assert r["agent_core_subject"] == "community lessons"
    assert r["revised_in_phase2"] is True
    assert r["agrees_with_bank"] is None


def test_no_label_anywhere_means_no_phase2(run, ctx):
    _, q, _, _ = ctx
    bare = {**q, "core_subject": None}
    r = run([
        [call("find_similar", {"query": "x", "k": 1}, "1")],
        [call("submit_decision", {**D, "core_subject": "lessons",
                                  "evidence_ids": [111]}, "2")],
    ], question=bare)
    assert r["phase2_against"] is None
    assert r["revised_in_phase2"] is None        # None = phase 2 never ran


def test_sample_questions_never_returns_the_question_itself():
    """The first gold run leaked: 20 of 32 questions got their own row - and
    so the bank's label - back from sample_questions, and 16 of those 20
    answered with that label."""
    db = store.session()
    q = next((x for x in store.queue(db, limit=50) if x["core_subject"]), None)
    if q is None:
        pytest.skip("no labelled question")
    everyone = store.sample_questions(db, q["core_subject"], n=500)
    others = store.sample_questions(db, q["core_subject"], n=500, exclude=q["f_id"])
    assert q["f_id"] in {r["f_id"] for r in everyone}
    assert q["f_id"] not in {r["f_id"] for r in others}
    assert len(others) == len(everyone) - 1


def test_analysis_fields_reach_the_report(run):
    r = run([
        [call("find_similar", {"query": "x", "k": 1}, "1")],
        [call("submit_decision", {**D, "core_subject": "lessons",
                                  "decisive_features": "cinema named; horaires",
                                  "rule_applied": "films in cinema",
                                  "evidence_ids": [111]}, "2")],
        [call("submit_decision", {**D, "core_subject": "lessons",
                                  "decisive_features": "cinema named; horaires",
                                  "rule_applied": "films in cinema",
                                  "evidence_ids": [111]}, "3")],
    ])
    assert r["decisive_features"] == "cinema named; horaires"
    assert r["rule_applied"] == "films in cinema"
    assert r["request_clause"] == "rc"


def test_schema_puts_the_analysis_before_the_label():
    """Structured output is written in schema order - the analysis has to come
    first or it becomes a justification of a label already chosen."""
    from .tools import TOOLS
    props = list(next(t for t in TOOLS if t["name"] == "submit_decision")
                 ["parameters"]["properties"])
    assert props.index("decisive_features") < props.index("core_subject")
    assert props.index("rule_applied") < props.index("core_subject")


def test_instructions_carry_the_shared_method():
    """The METHOD comes from RULES_CORE_SUBJECT, so it cannot drift from what
    llm.py and jev_labels.py tell their models."""
    from .agent import PHASE1
    assert "Find the request clause" in PHASE1
    assert "never overrides a note" in PHASE1


def test_prompt_shows_the_extracted_hints():
    from .agent import _prompt
    q = {"text": "Je suis un(e) collègue. Je viens de voir un film au cinéma. "
                 "Vous me demandez des informations (histoire, acteurs, "
                 "horaires, etc.) .", "theme": "Media & reading"}
    p = _prompt(q, [], [])
    assert "Hints: histoire, acteurs, horaires" in p
    assert "Request clause: Vous me demandez des informations" in p
