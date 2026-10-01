"""The same labelling agent, as a LangGraph graph.

Identical behaviour to `agent.py`: blind phase 1, informed phase 2, the same
four read tools, the same `submit_decision` schema, the same evidence
push-back, the same caps. What changes is who owns the loop.

    agent.py    a `for step in range(max_steps)` you can read top to bottom
    graph.py    nodes and edges; LangGraph owns the cycle

Both import `store` and `ToolBox` unchanged. That is the point worth noticing:
the tools, the schemas and the prompts - days of work - are the investment,
and the control flow is a file you can swap. Which of the two is better is an
empirical question about accuracy and cost, not an architectural preference.

    ┌───────┐   tool calls    ┌───────┐
    │ agent │ ──────────────► │ tools │
    │       │ ◄────────────── │       │
    └───┬───┘                 └───────┘
        │ terminal tool
        ▼
    ┌──────┐  phase 1 done, a current label exists ──► back to agent
    │ gate │  otherwise ─────────────────────────────► END
    └──────┘

What LangGraph gives you here that the hand-written loop does not:
  * `recursion_limit` - a cap enforced by the runtime, not by your counter
  * a checkpointer (not wired up) would make a run resumable mid-question;
    run.py resumes per question, which for this job is enough
  * `.stream()` for free, if you ever want live progress

What it costs: the loop is now spread across four functions and a router, and
a failure is a graph trace rather than a stack trace.
"""
from __future__ import annotations

import json
from typing import Annotated, Any, Optional, TypedDict

from langchain_core.messages import (AIMessage, HumanMessage, SystemMessage,
                                     ToolMessage)
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from .agent import PHASE1, Config, _prompt, phase2_message
from .tools import TERMINAL, TOOLS, ToolBox


def lc_tools() -> list[dict]:
    """The Responses-API schemas from tools.py, in the shape bind_tools reads.

    Same definitions, re-wrapped - not a second copy. A second copy is how you
    end up with a tool whose description says one thing in one runtime and
    another thing in the other.
    """
    return [{"type": "function",
             "function": {"name": t["name"],
                          "description": t["description"],
                          "parameters": t["parameters"]}}
            for t in TOOLS]


class State(TypedDict):
    messages: Annotated[list, add_messages]
    box: Any                      # the per-question ToolBox
    phase: int
    blind: Optional[dict]
    final: Optional[dict]
    action: Optional[str]
    nudged: bool
    steps: list[str]
    phase2_msg: Optional[str]
    instructions: str


def _terminal_call(msg) -> Optional[dict]:
    for c in getattr(msg, "tool_calls", None) or []:
        if c["name"] in TERMINAL:
            return c
    return None


def build(model) -> Any:
    """Compile the graph once. `model` is any LangChain chat model with
    tools already bound."""

    def agent(state: State) -> dict:
        msgs = [SystemMessage(state["instructions"])] + state["messages"]
        reply = model.invoke(msgs)
        return {"messages": [reply]}

    def tools(state: State) -> dict:
        box, out, steps = state["box"], [], []
        for c in state["messages"][-1].tool_calls:
            if c["name"] in TERMINAL:
                continue
            result = box.run(c["name"], c["args"])
            steps.append(f"{c['name']}({json.dumps(c['args'], ensure_ascii=False)[:70]})")
            out.append(ToolMessage(content=result, tool_call_id=c["id"]))
        return {"messages": out, "steps": state["steps"] + steps}

    def gate(state: State) -> dict:
        """A terminal tool fired. Push back once if it cites nothing, move to
        phase 2 if there is something independent to compare against, or stop.
        """
        box = state["box"]
        call = _terminal_call(state["messages"][-1])
        args, steps = call["args"], list(state["steps"])

        # every tool_call needs a reply, terminal ones included
        if (call["name"] == "submit_decision" and not state["nudged"]
                and (not box.retrieved or not args.get("evidence_ids"))):
            why = ("You have not retrieved anything yet" if not box.retrieved
                   else "evidence_ids is empty")
            steps.append(f"pushed back: {why}")
            return {"messages": [ToolMessage(
                        content=json.dumps({"error": f"{why}, so this "
                            "decision cites no evidence. Use find_similar or "
                            "sample_questions, then submit again with the "
                            "f_ids you actually saw."}),
                        tool_call_id=call["id"])],
                    "nudged": True, "steps": steps}

        steps.append(call["name"])
        ack = ToolMessage(content=json.dumps({"status": "recorded"}),
                          tool_call_id=call["id"])

        if (state["phase"] == 1 and call["name"] == "submit_decision"
                and state["phase2_msg"]):
            return {"messages": [ack, HumanMessage(state["phase2_msg"])],
                    "phase": 2, "blind": args, "action": call["name"],
                    "nudged": False, "steps": steps}

        return {"messages": [ack], "final": args, "action": call["name"],
                "blind": state["blind"] if state["phase"] == 2 else args,
                "steps": steps}

    def route_agent(state: State) -> str:
        last = state["messages"][-1]
        if _terminal_call(last):
            return "gate"
        if getattr(last, "tool_calls", None):
            return "tools"
        return "nudge"                       # prose instead of a tool call

    def nudge(state: State) -> dict:
        return {"messages": [HumanMessage(
                    "Answer by calling submit_decision or defer_to_human.")],
                "steps": state["steps"] + ["prose answer, re-prompted"]}

    def route_gate(state: State) -> str:
        return END if state["final"] is not None else "agent"

    g = StateGraph(State)
    g.add_node("agent", agent)
    g.add_node("tools", tools)
    g.add_node("gate", gate)
    g.add_node("nudge", nudge)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route_agent,
                            {"tools": "tools", "gate": "gate",
                             "nudge": "nudge"})
    g.add_edge("tools", "agent")
    g.add_edge("nudge", "agent")
    g.add_conditional_edges("gate", route_gate, {"agent": "agent", END: END})
    return g.compile()


def chat_model(cfg: Config):
    """ChatOpenAI over the Responses API, so reasoning items survive the same
    way they do in agent.py - dropping them makes the model re-derive its plan
    on every turn."""
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=cfg.model, reasoning_effort=cfg.effort,
                      use_responses_api=True).bind_tools(lc_tools())


def label_question(question: dict, db, index, client, cfg: Config,
                   subjects: list[dict], all_themes: list[str],
                   graph=None, model=None) -> dict:
    """Same signature and same return shape as agent.label_question, so run.py
    can call either one. `client` is used only for the tools' own API calls
    (embeddings, the blind breakdown); the loop itself goes through `model`.
    """
    from langgraph.errors import GraphRecursionError

    names = [s["name"] for s in subjects]
    box = ToolBox(db, index, client, cfg.embed_model, cfg.helper_model,
                  question, names, cfg.max_find_similar, cfg.vocab)
    graph = graph or build(model or chat_model(cfg))
    p2_msg, p2_against = phase2_message(question) if cfg.phase2 else (None, None)

    init: State = {
        "messages": [HumanMessage(_prompt(question, subjects, all_themes))],
        "box": box, "phase": 1, "blind": None, "final": None, "action": None,
        "nudged": False, "steps": [],
        "phase2_msg": p2_msg,
        "instructions": PHASE1.replace("<THEME>", question["theme"]),
    }

    try:
        # LangGraph's own cap, counted in node visits rather than turns: one
        # model turn is agent + (tools | gate), so budget roughly 3x.
        out = graph.invoke(init, {"recursion_limit": cfg.max_steps * 3})
        steps = out["steps"]
    except GraphRecursionError:
        out, steps = dict(init), init["steps"] + [
            f"stop: recursion_limit ({cfg.max_steps * 3} node visits)"]

    final = out.get("final") or {}
    blind = out.get("blind") or {}
    cited = set(final.get("evidence_ids") or [])
    usage = _usage(out.get("messages", []))

    return {
        "f_id": question["f_id"],
        "theme": question["theme"],
        "current_core_subject": question.get("core_subject"),
        "phase2_against": p2_against,
        "action": out.get("action") if out.get("final") else None,
        "blind_action": out.get("action"),
        "blind_core_subject": blind.get("core_subject"),
        "agent_core_subject": final.get("core_subject"),
        "request_clause": final.get("request_clause"),
        "decisive_features": final.get("decisive_features"),
        "rule_applied": final.get("rule_applied"),
        "is_new": final.get("is_new"),
        "confidence": final.get("confidence"),
        "reasoning": final.get("reasoning"),
        "rejected_candidates": final.get("rejected_candidates"),
        "evidence_ids": sorted(cited),
        "evidence_ok": bool(cited) and cited <= box.retrieved,
        "uncited_ids": sorted(cited - box.retrieved),
        "theme_looks_wrong": final.get("theme_looks_wrong"),
        "suggested_theme": final.get("suggested_theme") or None,
        "defer_reason": (final.get("reason")
                         if out.get("action") == "defer_to_human" else None),
        "agrees_with_bank": (
            None if not question.get("core_subject")
            or out.get("action") != "submit_decision" or not final
            else final.get("core_subject") == question["core_subject"]),
        # None means phase 2 never ran - the same meaning agent.py gives it.
        # blind == final is not enough to tell: with no phase 2 they are the
        # same answer, which is not the same thing as "kept it when asked"
        "revised_in_phase2": (
            None if out.get("phase") != 2 or not blind or not final
            else final.get("core_subject") != blind.get("core_subject")),
        "steps": steps,
        "model_calls": usage["calls"] + box.extra_calls,
        "find_similar_calls": box.find_similar_calls,
        "input_tokens": usage["input"],
        "output_tokens": usage["output"],
        "total_tokens": usage["input"] + usage["output"],
        "runtime": "langgraph",
    }


def _usage(messages) -> dict:
    """LangChain puts token counts on each AIMessage rather than on a response
    object, so they are summed here instead of accumulated as we go."""
    tot = {"input": 0, "output": 0, "calls": 0}
    for m in messages:
        if isinstance(m, AIMessage):
            tot["calls"] += 1
            u = getattr(m, "usage_metadata", None) or {}
            tot["input"] += u.get("input_tokens", 0)
            tot["output"] += u.get("output_tokens", 0)
    return tot
