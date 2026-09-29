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

D = dict(is_new=False, rejected_candidates=[], confidence=0.8, reasoning="r",
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

    def go(script, **cfg):
        c = Config(**cfg)
        if request.param == "plain":
            return plain.label_question(q, db, FakeIndex(),
                                        FakeClient(script), c, subs, themes)
        return lg.label_question(q, db, FakeIndex(), FakeClient([]), c,
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
