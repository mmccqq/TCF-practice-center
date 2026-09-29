"""
Minimal AI agent loop: an error-ledger language tutor (OpenAI version).
pip install openai   |   export OPENAI_API_KEY=...
"""
import json
from openai import OpenAI

client = OpenAI()
MODEL = "gpt-5.6-luna"
MAX_STEPS = 10  # safety cap: never let an agent loop forever

# --- 1. The environment: a fake ledger (swap for SQLite later) ---
LEDGER = {"luo": [{"type": "irregular past tense",
                   "example": "runned -> ran", "count": 3}]}

def get_ledger(learner_id):
    return LEDGER.get(learner_id, [])

def log_error(learner_id, type, examples):
    entries = LEDGER.setdefault(learner_id, [])
    for e in entries:
        if e["type"] == type:
            e["count"] += len(examples)
            return {"status": "ok", "new_count": e["count"]}
    entries.append({"type": type, "example": examples[0], "count": len(examples)})
    return {"status": "ok", "new_count": len(examples)}

TOOL_FUNCS = {"get_ledger": get_ledger, "log_error": log_error}

# --- 2. Tool schemas (Responses API format: flat, "parameters" key) ---
TOOLS = [
    {"type": "function",
     "name": "get_ledger",
     "description": "Read a learner's history of recurring errors.",
     "parameters": {"type": "object",
                    "properties": {"learner_id": {"type": "string"}},
                    "required": ["learner_id"],
                    "additionalProperties": False},
     "strict": True},
    {"type": "function",
     "name": "log_error",
     "description": "Record an error pattern the learner just made.",
     "parameters": {"type": "object",
                    "properties": {"learner_id": {"type": "string"},
                                   "type": {"type": "string"},
                                   "examples": {"type": "array",
                                                "items": {"type": "string"}}},
                    "required": ["learner_id", "type", "examples"],
                    "additionalProperties": False},
     "strict": True},
]

INSTRUCTIONS = ("You are an English tutor for learner_id 'luo'. Check the ledger, "
                "log new errors, then give a short correction and one targeted drill.")

# --- 3. The agent loop ---
def run_agent(user_text):
    history = [{"role": "user", "content": user_text}]

    for step in range(MAX_STEPS):
        response = client.responses.create(
            model=MODEL,
            instructions=INSTRUCTIONS,
            tools=TOOLS,
            input=history,
            reasoning={"effort": "low"},  # Luna: none/low/medium/high/xhigh/max
        )
        # Keep ALL output items (incl. reasoning items) in the history
        history += response.output

        calls = [item for item in response.output if item.type == "function_call"]

        # The MODEL decides when we're done: no tool calls = final answer
        if not calls:
            return response.output_text

        # Otherwise: execute every tool call and feed results back
        for call in calls:
            print(f"[step {step}] {call.name}({call.arguments})")
            try:
                out = TOOL_FUNCS[call.name](**json.loads(call.arguments))
                output = json.dumps(out)
            except Exception as e:  # errors are feedback too
                output = json.dumps({"error": str(e)})
            history.append({"type": "function_call_output",
                            "call_id": call.call_id,
                            "output": output})

    return "Stopped: hit MAX_STEPS."


if __name__ == "__main__":
    print(run_agent("I goed to the store yesterday and buyed apples."))
    print("\nLedger now:", LEDGER)