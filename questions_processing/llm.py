#!/usr/bin/env python3
"""
Run an LLM task over a JSONL of TCF questions.

The task - what to ask for - lives in llm_tasks.py. This file is the engine:
chunking, the alignment check that makes chunking safe, resume-by-id, four
providers, two batch dialects, and cost estimation. Task and provider vary
independently:

    # try the topic rules on 40 questions, free, immediate
    python3 llm.py sync questions_reussir/tache2.jsonl -t topic -p gemini \
        --limit 40 --chunk 40

    # what would the whole corpus cost?
    python3 llm.py estimate questions_reussir/tache2.jsonl -t topic --chunk 40

    # the real run: submit a batch, wait, write results
    python3 llm.py run questions_reussir/tache2.jsonl -t topic --chunk 40

    # resume after a crash (state, including task and provider, in <out>.batch)
    python3 llm.py fetch questions_reussir/tache2.jsonl -t topic

Input is any JSONL with `id` and `text`. Output defaults to
<input stem>_<task>.jsonl - one file per task, so two tasks never collide and
"already done" always means "already done *for this task*".

Re-running skips ids already present in the output, so a partial run resumes
and failures are retried simply by running it again.


COMMANDS
    estimate   count tokens on a sample and price the run. Costs nothing.
    run        submit every pending row as a batch, wait, write results.
    fetch      collect a batch submitted earlier by `run`.
    sync       send chunks one at a time and print each answer as it arrives.
    selftest   every task against every provider, offline. No API call.

PARAMETERS
    input                   (required) JSONL file with `id` and `text`.

    -t, --task NAME         topic (default) | abstract. Defined in llm_tasks.py.

    -p, --provider NAME     anthropic (default) | openai | deepseek | gemini.

    -m, --model NAME        Default is the provider's own: claude-opus-5,
                            gpt-5, deepseek-chat, gemini-2.5-flash.

    -o, --output PATH       Default <input stem>_<task>.jsonl. Also fixes where
                            the batch state file lives (<output>.batch).

    --chunk N               Rows per request. Default 1. Larger is much cheaper
                            (the prompt stops being re-sent) but loses more
                            work when a chunk is rejected. 20-40 is reasonable.

    --limit N               Only the first N *pending* rows. For trying things
                            out; combine with --chunk.

    --max-tokens N          Output budget per request, overriding 1500+100*chunk.
                            Raise this on "ran out of output budget" - thinking
                            tokens count against it.

    --reasoning LEVEL       reasoning_effort for OpenAI-compatible providers
                            (none / minimal / low). Stops a thinking model
                            spending the budget on a labelling task.

    --json-mode MODE        schema | object. Override how structured output is
                            requested, for a provider that rejects json_schema.

    --poll SECONDS          `run`/`fetch` only. Seconds between checks. Default 60.
    --batch-id ID           `fetch` only. Default: read from <output>.batch.
    --sync                  `estimate` only. Price at standard, not batch, rates.

Why batches: a few thousand short, independent, non-urgent requests is exactly
the Batch API's case - 50% of standard price, results within an hour (24h
ceiling). `sync` is for trying a prompt out, and is the only option on a
provider without a batch API.

Why --chunk: at chunk 1 the prompt is re-sent with every single row and
dominates the bill; the rows themselves average ~60 tokens. Chunking 40 sends
it 26 times instead of 1014. The risk is misalignment - a model answering 39
of 40, or reordering them. Each row is numbered and each answer must carry its
number back; a chunk whose numbers don't match exactly is discarded whole and
its rows stay pending, so a re-run retries them.

Providers, all producing the same output file:

    anthropic   default. Schema-enforced structured output; inline batch API.
    openai      schema-enforced; file-based batch API.
    deepseek    much cheaper, but JSON *mode* rather than schema enforcement,
                and no batch API - use `sync`.
    gemini      free tier, rate-limited per minute; no batch API here either.

The last three speak OpenAI's wire format, so they share one class and differ
only by base_url, model and a couple of flags.

Install and auth:
    pip install anthropic                    # -p anthropic
    pip install openai                       # -p openai / deepseek / gemini
    export ANTHROPIC_API_KEY=sk-ant-...      # or: ant auth login
    export OPENAI_API_KEY=sk-...
    export DEEPSEEK_API_KEY=sk-...
    export GEMINI_API_KEY=...                # free key from aistudio.google.com
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from llm_tasks import ENVELOPE, TASKS, Task

DEFAULT_CHUNK = 1


# Reasoning/thinking tokens count against the output ceiling on every current
# model, and they are the reason this is so much larger than the answers need:
# 40 labels are ~320 tokens, but the reasoning that produced them can be
# thousands. When a run reports "ran out of output budget", raise this with
# --max-tokens before touching --chunk - a smaller chunk also shrinks the
# budget, so it usually makes the problem worse rather than better.
MAX_TOKENS_OVERRIDE: int | None = None

# --debug: dump the provider's whole response whenever a chunk fails. The
# decoded message says what went wrong; this says what the provider actually
# sent, which is what you need when the two disagree.
DEBUG = False

# --extra: vendor-specific fields the OpenAI shape has no slot for, e.g.
# DeepSeek's {"thinking": {"type": "disabled"}}. These cannot be passed as
# top-level keyword arguments - the OpenAI SDK would reject an unknown kwarg -
# so the SDK path sends them through its `extra_body` escape hatch, while the
# batch path (which writes raw JSON, no SDK) merges them into the body itself.
EXTRA_BODY: dict = {}


def dump(label: str, obj) -> None:
    if not DEBUG:
        return
    body = obj if isinstance(obj, (str, dict)) else getattr(obj, "to_dict", lambda: obj)()
    print(f"  ~ {label} raw response:\n"
          + json.dumps(body, indent=1, ensure_ascii=False, default=str),
          file=sys.stderr)


def max_tokens_for(chunk: int) -> int:
    return MAX_TOKENS_OVERRIDE or (1500 + 100 * chunk)


# --------------------------------------------------------------------------
# input / output
# --------------------------------------------------------------------------

def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"not found: {path}")
    rows = []
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                sys.exit(f"{path}:{n}: invalid JSON - {exc}")
    return rows


def default_out(inp: Path, task: Task, model: str) -> Path:
    """One file per (task, model).

    The model belongs in the name because `pending()` skips ids already in the
    output: with a shared file, a second model asked to label the same rows
    would find them "done" and skip precisely the ones you wanted to compare.
    """
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", model)
    return inp.with_name(f"{inp.stem}_{task.name}_{safe}.jsonl")


def pending(inp: Path, out: Path, limit: int | None) -> list[dict]:
    """Rows still needing an abstract: those whose id is not in the output yet.

    This is what makes the script resumable and makes retrying failures free -
    a failed request simply never wrote a line, so the next run picks it up.
    """
    done = {r["id"] for r in read_jsonl(out)} if out.exists() else set()
    todo = [r for r in read_jsonl(inp)
            if r.get("id") is not None and r.get("text") and str(r["id"]) not in done]
    return todo[:limit] if limit else todo


def append(out: Path, records: list[dict]) -> None:
    with out.open("a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def chunked(rows: list[dict], size: int) -> list[list[dict]]:
    return [rows[i:i + size] for i in range(0, len(rows), size)]


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------

@dataclass
class Reply:
    """One provider's answer, reduced to what the shared code needs.

    Every provider-specific notion of "why there is no usable answer"
    (stop_reason, finish_reason, an HTTP error, a batch entry that expired)
    collapses into `error` here, so parse_chunk() never has to know which
    provider produced it.
    """
    text: str | None
    model: str
    error: str | None = None


class Provider:
    """What the rest of the program needs from an LLM API.

    Subclasses own the wire format and nothing else: building a request,
    turning a response into a Reply, and - if the API has one - driving a
    batch. Prompt text, chunking, alignment checking, resume logic and file
    output are shared and live above this line.
    """

    name: str
    default_model: str
    batch_discount: float = 0.5
    supports_batch: bool = True
    install_hint: str = ""
    key_env: str = ""

    # --- required of every provider -------------------------------------
    def request(self, rows: list[dict], model: str, task: Task) -> dict:
        raise NotImplementedError

    def count_tokens(self, rows: list[dict], model: str, task: Task) -> int | None:
        """Exact input-token count, or None if the API does not offer one."""
        return None

    def send(self, rows: list[dict], model: str, task: Task) -> Reply:
        raise NotImplementedError

    # --- only needed when supports_batch --------------------------------
    def submit(self, chunks: list[list[dict]], model: str, task: Task) -> str:
        raise NotImplementedError

    def wait(self, batch_id: str, poll: int) -> None:
        raise NotImplementedError

    def collect(self, batch_id: str) -> Iterator[tuple[str, Reply]]:
        raise NotImplementedError

    # --- shared helpers --------------------------------------------------
    def _missing(self) -> "SystemExit":
        return SystemExit(f"the {self.name} provider needs `{self.install_hint}`")

    def _no_key(self) -> "SystemExit":
        names = (self.key_env,) if isinstance(self.key_env, str) else self.key_env
        return SystemExit(f"no credentials for {self.name}: set "
                          f"{' or '.join(names)}")


class AnthropicProvider(Provider):
    name = "anthropic"
    default_model = "claude-opus-5"
    install_hint = "pip install anthropic"
    key_env = "ANTHROPIC_API_KEY (or `ant auth login`)"

    def __init__(self) -> None:
        self._client = None

    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ModuleNotFoundError:
                raise self._missing() from None
            self._client = anthropic.Anthropic()
        return self._client

    def request(self, rows, model, task):
        return {
            "model": model,
            "max_tokens": max_tokens_for(len(rows)),
            # cache_control only bites if SYSTEM exceeds the model's minimum
            # cacheable prefix (512-4096 tokens); below that it does nothing.
            "system": [{"type": "text", "text": task.prompt,
                        "cache_control": {"type": "ephemeral"}}],
            # effort "low": this is extraction, not reasoning - the recommended
            # way to cut cost rather than disabling thinking outright
            "output_config": {"effort": "low",
                              "format": {"type": "json_schema", "schema": task.schema()}},
            "messages": [{"role": "user", "content": task.body(rows)}],
        }

    def count_tokens(self, rows, model, task):
        return self.client().messages.count_tokens(
            model=model, system=task.prompt,
            messages=self.request(rows, model, task)["messages"],
        ).input_tokens

    def _reply(self, message) -> Reply:
        if message.stop_reason != "end_turn":
            dump(self.name, message)
        if message.stop_reason == "refusal":
            cat = getattr(message.stop_details, "category", None)
            return Reply(None, message.model, f"refused ({cat})")
        if message.stop_reason == "max_tokens":
            return Reply(None, message.model, "hit max_tokens - lower --chunk")
        text = next((b.text for b in message.content if b.type == "text"), None)
        return Reply(text, message.model, None if text else "no text block")

    def send(self, rows, model, task):
        client = self.client()      # first, so a missing SDK reports itself properly
        import anthropic
        try:
            return self._reply(client.messages.create(**self.request(rows, model, task)))
        except anthropic.RateLimitError as exc:
            retry = int(exc.response.headers.get("retry-after", "60"))
            print(f"  rate limited, sleeping {retry}s", file=sys.stderr)
            time.sleep(retry)
            return Reply(None, model, "rate limited")
        except anthropic.APIStatusError as exc:
            return Reply(None, model, f"{exc.status_code} {exc.message}")

    def submit(self, chunks, model, task):
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request
        batch = self.client().messages.batches.create(requests=[
            Request(custom_id=f"chunk-{i}",
                    params=MessageCreateParamsNonStreaming(**self.request(c, model, task)))
            for i, c in enumerate(chunks)
        ])
        return batch.id

    def wait(self, batch_id, poll):
        while True:
            batch = self.client().messages.batches.retrieve(batch_id)
            if batch.processing_status == "ended":
                return
            c = batch.request_counts
            print(f"  {batch.processing_status}: {c.processing} processing, "
                  f"{c.succeeded} done, {c.errored} errored", flush=True)
            time.sleep(poll)

    def collect(self, batch_id):
        for result in self.client().messages.batches.results(batch_id):
            if result.result.type != "succeeded":
                yield result.custom_id, Reply(None, self.default_model, result.result.type)
            else:
                yield result.custom_id, self._reply(result.result.message)


class OpenAICompatible(Provider):
    """OpenAI's wire format, which DeepSeek and several others also speak.

    Two things vary between them and are configured per instance rather than
    subclassed: whether the API can enforce a JSON *schema* or only guarantee
    valid JSON, and whether it has a batch API at all.
    """

    def __init__(self, *, name, base_url, key_env, default_model,
                 schema_mode="json_schema", token_param="max_completion_tokens",
                 supports_batch=True, reasoning_effort=None):
        self.name = name
        self.base_url = base_url
        self.key_env = key_env
        self.default_model = default_model
        self.schema_mode = schema_mode
        self.token_param = token_param
        self.supports_batch = supports_batch
        # None = don't send the parameter at all, which is right for models
        # that don't accept it. "low"/"none" is what stops a thinking model
        # from spending the whole output budget on a labelling task.
        self.reasoning_effort = reasoning_effort
        self.install_hint = "pip install openai"
        self._client = None

    def client(self):
        if self._client is None:
            import os
            try:
                import openai
            except ModuleNotFoundError:
                raise self._missing() from None
            # key_env may name several variables - Google, for instance, is
            # documented under both GEMINI_API_KEY and GOOGLE_API_KEY
            names = (self.key_env,) if isinstance(self.key_env, str) else self.key_env
            key = next((os.environ[n] for n in names if os.environ.get(n)), None)
            if not key:
                raise self._no_key()
            self._client = openai.OpenAI(api_key=key, base_url=self.base_url)
        return self._client

    def request(self, rows, model, task):
        if self.schema_mode == "json_schema":
            # strict schema enforcement; JSON_SCHEMA already satisfies its
            # requirements (every property required, additionalProperties false)
            fmt = {"type": "json_schema",
                   "json_schema": {"name": ENVELOPE, "strict": True,
                                   "schema": task.schema()}}
        else:
            # JSON *mode*: valid JSON is guaranteed, the shape is not. The
            # schema is described in the prompt instead, and parse_chunk's
            # alignment check is what actually protects the output.
            fmt = {"type": "json_object"}
        req = {
            "model": model,
            self.token_param: max_tokens_for(len(rows)),
            "response_format": fmt,
            # OpenAI-style APIs carry the system prompt as the first message
            # rather than as a separate top-level field
            "messages": [{"role": "system", "content": task.prompt},
                         {"role": "user", "content": task.body(rows)}],
        }
        if self.reasoning_effort and "reasoning_effort" not in EXTRA_BODY:
            # Gemini and OpenAI's reasoning models both accept this through the
            # OpenAI wire format; it is the knob that stops thinking from
            # eating the whole output budget on a labelling task. Whether a
            # given compatibility layer honours it - or silently drops it - is
            # what --debug is for.
            req["reasoning_effort"] = self.reasoning_effort
        return req

    @staticmethod
    def _spent(completion, cap: int) -> str:
        """Where the output budget went - the only useful thing to say when a
        reply was truncated, since reasoning tokens are invisible otherwise."""
        u = getattr(completion, "usage", None)
        if u is None:
            return f"budget was {cap}"
        detail = getattr(u, "completion_tokens_details", None)
        reasoning = getattr(detail, "reasoning_tokens", None) if detail else None
        out = getattr(u, "completion_tokens", "?")
        return (f"budget {cap}, generated {out}"
                + (f" of which {reasoning} reasoning" if reasoning else ""))

    def _reply(self, completion, cap: int = 0) -> Reply:
        choice = completion.choices[0]
        if choice.finish_reason != "stop":
            dump(self.name, completion)
        model = getattr(completion, "model", self.default_model)
        if choice.finish_reason == "length":
            return Reply(None, model,
                         f"ran out of output budget ({self._spent(completion, cap)}). "
                         f"Raise --max-tokens; lowering --chunk shrinks the budget too")
        if choice.finish_reason == "content_filter":
            return Reply(None, model, "blocked by content filter")
        text = choice.message.content
        return Reply(text, model, None if text else "empty response")

    def send(self, rows, model, task):
        client = self.client()      # first, so a missing SDK reports itself properly
        import openai
        # Free tiers rate-limit per minute, so being throttled is the normal
        # case rather than an error. Sleep and retry once before giving up on
        # the chunk; it stays pending either way, but one retry saves a re-run.
        req = self.request(rows, model, task)
        # the SDK validates its own kwargs, so vendor fields ride in extra_body
        if EXTRA_BODY:
            req = {**req, "extra_body": EXTRA_BODY}
        dump(f"{self.name} request", {k: ("<prompt>" if k == "messages" else v)
                                      for k, v in req.items()})
        for attempt in (1, 2):
            try:
                return self._reply(client.chat.completions.create(**req),
                                   req[self.token_param])
            except openai.RateLimitError as exc:
                wait = int(getattr(exc, "response", None)
                           and exc.response.headers.get("retry-after") or 30)
                if attempt == 2:
                    return Reply(None, model, "rate limited twice")
                print(f"  rate limited, sleeping {wait}s", file=sys.stderr)
                time.sleep(wait)
            except openai.APIStatusError as exc:
                dump(self.name, getattr(exc, "body", None) or str(exc))
                return Reply(None, model, f"{exc.status_code} {exc.message}")
        return Reply(None, model, "rate limited")           # unreachable

    # -- batch: OpenAI's is file-based, unlike Anthropic's inline requests --
    def submit(self, chunks, model, task):
        import io
        lines = "\n".join(
            json.dumps({"custom_id": f"chunk-{i}", "method": "POST",
                        "url": "/v1/chat/completions",
                        # raw HTTP here, no SDK: vendor fields belong in the body
                        "body": {**self.request(c, model, task), **EXTRA_BODY}},
                       ensure_ascii=False)
            for i, c in enumerate(chunks)
        )
        upload = self.client().files.create(
            file=("requests.jsonl", io.BytesIO(lines.encode("utf-8"))), purpose="batch")
        batch = self.client().batches.create(
            input_file_id=upload.id, endpoint="/v1/chat/completions",
            completion_window="24h")
        return batch.id

    def wait(self, batch_id, poll):
        while True:
            batch = self.client().batches.retrieve(batch_id)
            if batch.status in ("completed", "failed", "expired", "cancelled"):
                if batch.status != "completed":
                    print(f"  batch ended as {batch.status}", file=sys.stderr)
                return
            print(f"  {batch.status}: {batch.request_counts.completed} done, "
                  f"{batch.request_counts.failed} failed", flush=True)
            time.sleep(poll)

    def collect(self, batch_id):
        batch = self.client().batches.retrieve(batch_id)
        if not batch.output_file_id:
            print(f"  ! batch {batch_id} produced no output file", file=sys.stderr)
            return
        body = self.client().files.content(batch.output_file_id).text
        for line in body.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            cid = row.get("custom_id", "?")
            if row.get("error") or row.get("response", {}).get("status_code") != 200:
                yield cid, Reply(None, self.default_model, "errored")
                continue
            payload = row["response"]["body"]
            choice = payload["choices"][0]
            if choice.get("finish_reason") == "length":
                yield cid, Reply(None, payload.get("model", self.default_model),
                                 "ran out of output budget - raise --max-tokens")
                continue
            yield cid, Reply(choice["message"].get("content"),
                             payload.get("model", self.default_model))


PROVIDERS: dict[str, Provider] = {
    "anthropic": AnthropicProvider(),
    "openai": OpenAICompatible(
        name="openai", base_url=None, key_env="OPENAI_API_KEY",
        default_model="gpt-5.6-luna",
        # verify against https://openai.com/api/pricing before trusting `estimate`
    ),
    "deepseek": OpenAICompatible(
        name="deepseek", base_url="https://api.deepseek.com",
        key_env="DEEPSEEK_API_KEY", default_model="deepseek-v4-pro",
        # DeepSeek offers JSON mode, not schema enforcement, and has no batch
        # API - both were true at the time of writing; check their docs.
        schema_mode="json_object", token_param="max_tokens", supports_batch=False,
        reasoning_effort="low", 
    ),
    # Google publishes an OpenAI-compatible endpoint for Gemini, so it needs no
    # new code - only this entry. AI Studio has a free tier, rate-limited per
    # minute rather than capped in dollars, which is what `sync` plus the
    # retry in send() is for. Check the current model id and the free-tier
    # limits at ai.google.dev before a long run.
    "gemini": OpenAICompatible(
        name="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        key_env=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        default_model="gemini-3.8-flash",   # $0 on the free tier
        token_param="max_tokens", supports_batch=False,
    ),
}


# --------------------------------------------------------------------------
# shared: turn a Reply into records, or reject it
# --------------------------------------------------------------------------

def parse_chunk(reply: Reply, ids: list[str], label: str, task: Task) -> list[dict]:
    """Map a chunk's reply back onto the ids that were sent, or reject it.

    Batching many questions into one request trades cost for the risk that the
    model drops, merges or reorders items. `n` is checked against exactly the
    set that was sent, and a chunk that fails is returned empty rather than
    partially: the ids simply stay pending and the next run retries them.
    Silently accepting 38 answers to 40 questions is the failure worth
    preventing here, because nothing downstream would ever notice.

    Provider-agnostic on purpose: the Reply has already reduced every wire
    format's failure modes to `error`.
    """
    if reply.error or reply.text is None:
        print(f"  ! {label}: {reply.error or 'no text'}", file=sys.stderr)
        return []

    # A provider offering JSON mode rather than schema enforcement can return
    # well-formed JSON of the wrong shape, so this must not be allowed to raise.
    try:
        items = json.loads(reply.text)[ENVELOPE]
        got = {int(it["n"]) for it in items}
        # read the answer field here too: in JSON mode the model can follow the
        # prompt's example key instead of the schema's, and a KeyError raised
        # after the alignment check would abort the whole run rather than
        # voiding one chunk
        answers = {int(it["n"]): it[task.answer_key] for it in items}
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        print(f"  ! {label}: unusable JSON - {exc}", file=sys.stderr)
        return []

    want = set(range(1, len(ids) + 1))
    if got != want:
        missing, extra = sorted(want - got), sorted(got - want)
        print(f"  ! {label}: expected {len(ids)} answers, got {len(got)}"
              f"{f', missing {missing}' if missing else ''}"
              f"{f', unexpected {extra}' if extra else ''}", file=sys.stderr)
        return []

    return [{**task.record(ids[n - 1], answers[n], reply.model)}
            for n in sorted(answers)]


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def _resolve(args) -> tuple[Provider, Task, Path]:
    """Provider, task and output path - everything but the pending rows.

    `fetch` needs the path in order to find the state file, but has no reason
    to read the input, so the two share this and nothing else.
    """
    prov = PROVIDERS[args.provider]
    task = TASKS[args.task]
    args.model = args.model or prov.default_model
    # Whether a given endpoint enforces a JSON schema or only guarantees valid
    # JSON is the thing most likely to have changed since this was written, so
    # it is overridable without editing the table.
    if getattr(args, "json_mode", None) and isinstance(prov, OpenAICompatible):
        prov.schema_mode = "json_schema" if args.json_mode == "schema" else "json_object"
    if getattr(args, "reasoning", None) and isinstance(prov, OpenAICompatible):
        prov.reasoning_effort = args.reasoning
    if getattr(args, "max_tokens", None):
        global MAX_TOKENS_OVERRIDE
        MAX_TOKENS_OVERRIDE = args.max_tokens
    if getattr(args, "debug", False):
        global DEBUG
        DEBUG = True
    if getattr(args, "extra", None):
        global EXTRA_BODY
        try:
            EXTRA_BODY = json.loads(args.extra)
        except json.JSONDecodeError as exc:
            sys.exit(f"--extra is not valid JSON: {exc}")
        if not isinstance(EXTRA_BODY, dict):
            sys.exit("--extra must be a JSON object")
    return prov, task, args.output or default_out(args.input, task, args.model)


def _setup(args) -> tuple[Provider, Task, Path, list[dict]]:
    prov, task, out = _resolve(args)
    return prov, task, out, pending(args.input, out, args.limit)


def cmd_estimate(args) -> None:
    prov, task, out, todo = _setup(args)
    if not todo:
        print(f"nothing to do - every id already has a {task.name}")
        return

    chunks = chunked(todo, args.chunk)
    # count a sample of whole chunks rather than all of them: count_tokens is
    # free but still one round trip per call, and these prompts are near-uniform
    sample = chunks[: min(5, len(chunks))]
    counts = [prov.count_tokens(c, args.model, task) for c in sample]
    if counts[0] is None:
        # no token-counting endpoint; French runs about 3.2 characters/token
        avg_in = sum(len(task.prompt) + len(task.body(c)) for c in sample) / len(sample) / 3.2
        source = "estimated from characters"
    else:
        avg_in = sum(counts) / len(counts)
        source = f"counted over {len(sample)} chunk(s)"

    # ~25 tokens of answer per question, plus a fixed slice of thinking
    avg_out = 40 + 25 * args.chunk
    total_in = avg_in * len(chunks)
    total_out = avg_out * len(chunks)
    batched = prov.supports_batch and not args.sync

    print(f"{len(todo)} row(s) still need a {task.name}")
    print(f"  provider         {prov.name}")
    print(f"  model            {args.model}")
    print(f"  chunk size       {args.chunk}  ->  {len(chunks)} request(s)")
    print(f"  input            {total_in:>10,.0f} tokens  "
          f"({avg_in:.0f}/request, {source})")
    print(f"  output           {total_out:>10,.0f} tokens  "
          f"({avg_out}/request, assumed)")
    print(f"  pricing          {'batch, usually half the standard rate' if batched else 'standard'}")

    if args.price:
        try:
            in_price, out_price = (float(x) for x in args.price.split(","))
        except ValueError:
            sys.exit("--price wants two numbers, e.g. --price 0.27,1.10")
        rate = prov.batch_discount if batched else 1.0
        cost = rate * (total_in * in_price + total_out * out_price) / 1e6
        print(f"  at {in_price}/{out_price} per Mtok:  ${cost:.2f}")
    else:
        # Deliberately no built-in rate card: published prices change often
        # enough that a table in this repo would be confidently wrong, which is
        # worse than absent. Tokens are a measurement; dollars are a lookup.
        print("\n  no price given, so no dollar figure. Multiply the tokens above")
        print("  by the current rate, or pass it in:  --price <in>,<out>  ($/Mtok)")

    print("\nOutput length is an assumption, not a measurement - run")
    print(f"`sync --limit {max(args.chunk * 2, 10)} --chunk {args.chunk}` first if it matters.")


def cmd_run(args) -> None:
    prov, task, out, todo = _setup(args)
    if not todo:
        print(f"nothing to do - every id already has a {task.name}")
        return
    if not prov.supports_batch:
        sys.exit(f"{prov.name} has no batch API - use `sync` instead "
                 f"(and consider a larger --chunk to compensate)")

    chunks = chunked(todo, args.chunk)
    batch_id = prov.submit(chunks, args.model, task)

    # The ids each chunk covers have to survive to `fetch`, which may run in a
    # different process days later: the reply carries positions, not ids, and
    # recomputing the chunking from the input would drift as soon as anything
    # else is written to the output file.
    state = Path(f"{out}.batch")
    state.write_text(json.dumps({
        "provider": prov.name,
        "task": task.name,
        "batch_id": batch_id,
        "chunks": {f"chunk-{i}": [str(r["id"]) for r in c] for i, c in enumerate(chunks)},
    }), encoding="utf-8")

    print(f"submitted {len(todo)} question(s) as {len(chunks)} request(s), batch {batch_id}")
    print(f"state saved to {state} - `fetch` resumes from it if this exits")

    prov.wait(batch_id, args.poll)
    _collect(prov, task, batch_id, out, state)


def cmd_fetch(args) -> None:
    _prov, _task, out = _resolve(args)
    state = Path(f"{out}.batch")
    if not state.exists():
        if args.batch_id:
            sys.exit(f"{state} is missing, so the chunk-to-id mapping is lost.\n"
                     f"Batch {args.batch_id} still exists server-side, but its replies\n"
                     f"carry positions rather than ids and cannot be reattached.")
        sys.exit(f"no batch id given and {state} does not exist")

    saved = json.loads(state.read_text(encoding="utf-8"))
    # the state file records which provider submitted it, so `fetch` needs no
    # -p and cannot be pointed at the wrong API by accident
    prov = PROVIDERS[saved.get("provider", "anthropic")]
    task = TASKS[saved.get("task", args.task)]
    batch_id = args.batch_id or saved["batch_id"]
    prov.wait(batch_id, args.poll)
    _collect(prov, task, batch_id, out, state)


def _collect(prov: Provider, task: Task, batch_id: str, out: Path, state: Path) -> None:
    chunks: dict[str, list[str]] = json.loads(state.read_text(encoding="utf-8"))["chunks"]
    records, failed = [], 0
    for custom_id, reply in prov.collect(batch_id):
        ids = chunks.get(custom_id, [])
        got = parse_chunk(reply, ids, custom_id, task)
        records += got
        failed += len(ids) - len(got)

    append(out, records)
    state.unlink(missing_ok=True)
    print(f"\nwrote {len(records)} {task.name}(s) to {out}")
    if failed:
        print(f"{failed} row(s) got no {task.name} - run the same command "
              f"again to retry just those")


def cmd_sync(args) -> None:
    """One request at a time. For trying the prompt out, not for the corpus -
    except on a provider with no batch API, where it is the only option."""
    prov, task, out, todo = _setup(args)
    if not todo:
        print(f"nothing to do - every id already has a {task.name}")
        return

    chunks = chunked(todo, args.chunk)
    records = []
    for i, c in enumerate(chunks, 1):
        label = f"chunk {i}/{len(chunks)}"
        got = parse_chunk(prov.send(c, args.model, task), [str(r["id"]) for r in c], label, task)
        records += got
        # print each abstract against its source, which is the point of `sync`
        for rec, row in zip(got, c):
            print(f"[{label}] {rec[task.output_field]}")
            print(f"          {row['text'][:88]}")

    append(out, records)
    print(f"\nwrote {len(records)} {task.name}(s) to {out}")


def cmd_selftest(_args) -> None:
    """Offline: every task against every provider, no API call."""
    import tempfile

    rows = [{"id": "aa", "text": "Sujet un."},
            {"id": "bb", "text": "Sujet deux."},
            {"id": "cc", "text": "Sujet trois."}]
    ids = [r["id"] for r in rows]

    assert [len(c) for c in chunked(rows, 1)] == [1, 1, 1]
    assert [len(c) for c in chunked(rows, 2)] == [2, 1], "last chunk may be short"
    assert [len(c) for c in chunked(rows, 10)] == [3]

    def parts(req: dict) -> tuple[str, str]:
        """(system text, user text), wherever this provider puts them."""
        user = next(m["content"] for m in req["messages"] if m["role"] == "user")
        system = next((m["content"] for m in req["messages"] if m["role"] == "system"), None)
        if system is None:                       # Anthropic: a top-level field
            system = "".join(b["text"] for b in req["system"])
        return system, user

    for tname, task in TASKS.items():
        # the worked example in the prompt must match the schema exactly, or a
        # JSON-mode provider follows the example and every chunk is voided.
        # This is the assertion that would have caught the topics/abstracts
        # mismatch that made this split worth doing.
        example = json.loads(task.prompt[task.prompt.index("{", task.prompt.index("Sortie")):])
        assert ENVELOPE in example, f"{tname}: example envelope != schema envelope"
        assert all(task.answer_key in item for item in example[ENVELOPE]), \
            f"{tname}: example answer key != schema answer key"
        assert task.schema()["required"] == [ENVELOPE], tname
        assert task.body(rows) == "1. Sujet un.\n2. Sujet deux.\n3. Sujet trois.", tname

        for pname, prov in PROVIDERS.items():
            req = prov.request(rows, prov.default_model, task)
            system, user = parts(req)
            assert user == task.body(rows), f"{pname}/{tname} mangled the rows"
            assert system == task.prompt, f"{pname}/{tname} lost the prompt"
            cap = req.get("max_tokens") or req.get("max_completion_tokens")
            assert cap == max_tokens_for(3), f"{pname}/{tname} output cap"

        # round trip: a well-formed reply must map back onto the right ids
        def item(n, value):
            return {"n": n, task.answer_key: value}

        def payload(items):
            return Reply(json.dumps({ENVELOPE: items}), "m")

        ok = payload([item(2, "B"), item(1, "A"), item(3, "C")])   # out of order
        got = parse_chunk(ok, ids, "t", task)
        assert [r["id"] for r in got] == ids, f"{tname}: n must map to id by position"
        assert [r[task.output_field] for r in got] == ["A", "B", "C"], tname
        assert set(got[0]) == {"id", task.output_field, "model"}, tname

        # every misalignment voids the whole chunk rather than part of it
        assert parse_chunk(payload([item(1, "A"), item(2, "B")]), ids, "t", task) == [], \
            f"{tname}: a dropped item must void the chunk"
        assert parse_chunk(payload([item(1, "A")] * 3), ids, "t", task) == [], \
            f"{tname}: repeated n must void the chunk"
        assert parse_chunk(payload([item(0, "A"), item(1, "B"), item(2, "C")]),
                           ids, "t", task) == [], f"{tname}: 0-based n must void the chunk"
        assert parse_chunk(Reply(None, "m", "refused"), ids, "t", task) == []
        # JSON mode can return valid JSON of the wrong shape - must not raise
        assert parse_chunk(Reply('{"results": []}', "m"), ids, "t", task) == []
        assert parse_chunk(Reply("not json at all", "m"), ids, "t", task) == []
        assert parse_chunk(payload([{task.answer_key: "A"}]), ids, "t", task) == []
        assert parse_chunk(payload([{"n": 1}]), ids, "t", task) == [], "missing answer key"

    # provider-shape differences the tasks must not disturb
    t = TASKS["topic"]
    assert PROVIDERS["anthropic"].request(rows, "m", t)["messages"][0]["role"] == "user"
    assert PROVIDERS["openai"].request(rows, "m", t)["messages"][0]["role"] == "system", \
        "OpenAI-style APIs carry the system prompt inside messages"
    assert PROVIDERS["deepseek"].request(rows, "m", t)["response_format"] == {"type": "json_object"}
    assert PROVIDERS["openai"].request(rows, "m", t)["response_format"]["type"] == "json_schema"
    assert not PROVIDERS["deepseek"].supports_batch
    assert not PROVIDERS["gemini"].supports_batch
    assert PROVIDERS["gemini"].base_url.endswith("/openai/"), \
        "gemini must point at Google's OpenAI-compatible endpoint, not the native one"

    with tempfile.TemporaryDirectory() as d:
        inp = Path(d) / "tache2.jsonl"
        inp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in [
            {"id": "1", "text": "Question une."},
            {"id": "2", "text": "Question deux."},
            {"id": "3", "text": ""},                 # no text -> never requested
        ]) + "\n", encoding="utf-8")

        # one output file per (task, model): running `abstract` must not mark
        # ids done for `topic`, and a second model must not skip the rows the
        # first one already labelled - that is the comparison set
        outs = {n: default_out(inp, t, "m1") for n, t in TASKS.items()}
        assert len(set(outs.values())) == len(TASKS), f"tasks share an output file: {outs}"
        t = TASKS["topic"]
        assert default_out(inp, t, "m1") != default_out(inp, t, "m2"), \
            "two models must not share an output file"
        assert default_out(inp, t, "gpt-5.6-luna").name.endswith("_gpt-5.6-luna.jsonl")
        assert "/" not in default_out(inp, t, "vendor/model:v1").name, "model must be sanitised"

        out = outs["topic"]
        assert [r["id"] for r in pending(inp, out, None)] == ["1", "2"], "empty text must be skipped"
        append(out, [{"id": "1", "topic": "work", "model": "m"}])
        assert [r["id"] for r in pending(inp, out, None)] == ["2"], "already-done ids must be skipped"
        assert read_jsonl(out)[0]["topic"] == "work"

    print(f"selftest passed ({len(TASKS)} tasks x {len(PROVIDERS)} providers)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, help_ in (("estimate", "count tokens and price the run"),
                        ("run", "submit a batch, wait, write results"),
                        ("fetch", "collect a batch submitted earlier"),
                        ("sync", "one request at a time, for trying the prompt")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("input", type=Path, help="JSONL with `id` and `text`")
        p.add_argument("-o", "--output", type=Path,
                       help="default: <input stem>_abstract.jsonl")
        p.add_argument("-p", "--provider", choices=tuple(PROVIDERS), default="anthropic")
        p.add_argument("-t", "--task", choices=tuple(TASKS), default="topic",
                       help="what to ask for; see llm_tasks.py")
        p.add_argument("-m", "--model", default=None,
                       help="default: the provider's own default")
        p.add_argument("--limit", type=int, help="only the first N pending rows")
        p.add_argument("--max-tokens", type=int, default=None, metavar="N",
                       help="output budget per request, overriding the "
                            "1500+100*chunk default. Raise this when a run "
                            "reports 'ran out of output budget' - thinking "
                            "tokens count against it")
        p.add_argument("--reasoning", default=None, metavar="LEVEL",
                       help="reasoning_effort for OpenAI-compatible providers "
                            "(e.g. none / minimal / low). Stops a thinking "
                            "model spending the output budget on a labelling "
                            "task. No effect on -p anthropic")
        p.add_argument("--price", default=None, metavar="IN,OUT",
                       help="`estimate` only. $/Mtok for this model, e.g. "
                            "--price 0.27,1.10 - look it up on the provider's "
                            "pricing page. Omitted, estimate reports tokens only")
        p.add_argument("--extra", default=None, metavar="JSON",
                       help="vendor-specific fields with no OpenAI-format "
                            "slot, sent via the SDK's extra_body. e.g. "
                            "--extra '{\"thinking\": {\"type\": \"disabled\"}}'")
        p.add_argument("--debug", action="store_true",
                       help="print the provider's raw response whenever a chunk "
                            "fails. For HTTP-level tracing instead, set "
                            "OPENAI_LOG=debug or ANTHROPIC_LOG=debug")
        p.add_argument("--json-mode", choices=("schema", "object"), default=None,
                       help="override how structured output is requested. Use "
                            "`object` if a provider rejects json_schema; the "
                            "alignment check catches the difference either way")
        p.add_argument("--chunk", type=int, default=DEFAULT_CHUNK, metavar="N",
                       help="questions per request (default %(default)s). Larger "
                            "chunks stop repeating the instructions and cut cost "
                            "sharply, at the risk of the model dropping or "
                            "reordering items - which is detected and voids the "
                            "whole chunk, so those questions stay pending. 20-40 "
                            "is a reasonable range")
        if name in ("run", "fetch"):
            p.add_argument("--poll", type=int, default=60, help="seconds between checks")
        if name == "fetch":
            p.add_argument("--batch-id", help="default: read from <output>.batch")
        if name == "estimate":
            p.add_argument("--sync", action="store_true",
                           help="price at standard rates instead of the batch "
                                "discount, i.e. what `sync` would cost")

    sub.add_parser("selftest", help="offline check of prompt and file handling")

    args = ap.parse_args()
    if getattr(args, "chunk", 1) < 1:
        sys.exit("--chunk must be at least 1")
    try:
        {"estimate": cmd_estimate, "run": cmd_run, "fetch": cmd_fetch,
         "sync": cmd_sync, "selftest": cmd_selftest}[args.cmd](args)
    except TypeError as exc:
        # the Anthropic SDK resolves credentials on the first request and
        # raises a bare TypeError; a traceback here says nothing about what to do
        if "authentication" not in str(exc).lower():
            raise
        sys.exit("no Anthropic credentials found.\n"
                 "  export ANTHROPIC_API_KEY=sk-ant-...\n"
                 "  or install the CLI and run:  ant auth login")
    except KeyboardInterrupt:
        # `run` may be mid-poll; the batch keeps going server-side
        sys.exit("\ninterrupted - the batch is still running; resume with `fetch`")


if __name__ == "__main__":
    main()
