#!/usr/bin/env python3
"""
Ask an LLM for a short abstract of each TCF question.

An abstract is a <=10-word French noun phrase naming the situation - what the
question is *about*, stripped of the role-play scaffolding every prompt shares
("Je suis votre ami(e)... Vous me posez des questions..."). It is what a list
view can show instead of 300 characters of near-identical boilerplate, and what
a human skims when judging whether two questions are the same.

    Je suis votre voisin(e). Vous venez d'arriver dans la ville et vous ne
    connaissez personne. Vous me demandez comment faire pour rencontrer de
    nouvelles personnes.
        -> "Rencontrer des gens dans une nouvelle ville"

    # what will this cost, and how many are left to do?
    python3 abstract.py estimate questions_reussir/tache2.jsonl --chunk 40

    # the real run: submit a batch, wait, write results
    python3 abstract.py run questions_reussir/tache2.jsonl --chunk 40

    # resume after a crash / a closed laptop (state kept in <out>.batch)
    python3 abstract.py fetch questions_reussir/tache2.jsonl

    # try the prompt on a handful, immediately, no batch
    python3 abstract.py sync questions_reussir/tache2.jsonl --limit 40 --chunk 40

    # a different provider - same prompt, same output format
    python3 abstract.py sync questions_reussir/tache2.jsonl -p deepseek --limit 40

Input is any JSONL with `id` and `text` - scraper output (tache2.jsonl) or
deduplicated clusters (tache2_tfidf.jsonl). Output defaults to
<input stem>_abstract.jsonl, one {"id", "abstract", "model"} per line.

Re-running skips ids already present in the output, so a partial run resumes
and failures are retried simply by running it again.

Why batches: this is a few thousand short, independent, non-urgent requests -
exactly the Batch API's case. It costs 50% of the standard price and returns
within an hour (24h ceiling). `sync` exists for trying the prompt out, not for
the full corpus.

Why --chunk: at chunk 1 the ~450-token instruction block is re-sent with every
single question and ends up dominating the bill - the questions themselves
average only ~60 tokens. Batching 40 per request sends the instructions 26
times instead of 1014, which is most of the cost:

    chunk   1   1014 requests   instructions sent 1014x
    chunk  40     26 requests   instructions sent 26x, ~4x cheaper overall

The risk it buys is misalignment - a model answering 39 of 40, or reordering
them. Each question is numbered and each answer must carry its number back;
a chunk whose numbers don't match exactly what was sent is discarded whole and
its questions stay pending, so a re-run retries them. Larger chunks are
cheaper but lose more work per failure; 20-40 is a reasonable range.

Providers (-p / --provider), all producing the same output file:

    anthropic   default. Structured output is schema-enforced; has a batch API.
    openai      schema-enforced; file-based batch API.
    deepseek    OpenAI-compatible wire format, much cheaper, but JSON *mode*
                rather than schema enforcement and no batch API - use `sync`.

Install and auth:
    pip install anthropic                    # -p anthropic
    pip install openai                       # -p openai / -p deepseek
    export ANTHROPIC_API_KEY=sk-ant-...      # or: ant auth login
    export OPENAI_API_KEY=sk-...
    export DEEPSEEK_API_KEY=sk-...
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

DEFAULT_CHUNK = 1


# Adaptive thinking is on by default on frontier models and its tokens count
# against the output ceiling, so leave headroom beyond the ~12 tokens an
# abstract needs.
def max_tokens_for(chunk: int) -> int:
    return 1500 + 100 * chunk


SYSTEM = """\
Tu resumes des sujets d'expression orale du TCF Canada.

Chaque sujet decrit une mise en situation: un role pour l'examinateur, un role
pour le candidat, et un theme sur lequel poser des questions. Presque tous
partagent la meme structure ("Je suis votre ami(e)... Vous me posez des
questions..."). Cette structure n'est pas informative: seul le theme l'est.

Pour chaque sujet numerote, produis une phrase nominale de 10 mots maximum,
en francais, qui nomme la situation. Pas de verbe conjugue, pas de "vous",
pas de ponctuation finale. Garde les mots concrets du sujet (le lieu,
l'objet, l'activite).

Reponds en JSON. Renvoie exactement un objet par sujet recu, avec le meme
numero `n`, dans le meme ordre. N'en omets aucun, n'en fusionne aucun, n'en
ajoute aucun.

Exemple d'entree:

1. Je suis votre voisin(e). Vous venez d'arriver dans la ville et vous ne
connaissez personne. Vous me demandez comment faire pour rencontrer de
nouvelles personnes.
2. Je travaille dans une agence de location de voitures, vous avez besoin
d'en louer une. Posez-moi des questions sur les conditions (prix, duree,
assurance, etc.).
3. Pensez-vous que les etablissements scolaires devraient valoriser davantage
les activites liees a l'art (musique, theatre, dessin) ? Pourquoi ?

Sortie correspondante:

{"abstracts": [
  {"n": 1, "abstract": "Rencontrer des gens dans une nouvelle ville"},
  {"n": 2, "abstract": "Louer une voiture en agence"},
  {"n": 3, "abstract": "Place des activites artistiques a l'ecole"}]}
"""

# The shape every provider is asked for. How it is *requested* differs (see
# each Provider.request); what comes back is validated identically either way.
#
# `n` is the question's 1-based position in *this request*, not its id. Two
# reasons: a short integer is far less error-prone for the model to echo than
# a 13-digit scraper id, and it costs a few tokens instead of a dozen. The
# mapping back to real ids is done locally, and a chunk whose returned `n`
# values don't match what was sent is rejected whole - see parse_chunk().
JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "abstracts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer",
                          "description": "the number the sujet was given in the request"},
                    "abstract": {
                        "type": "string",
                        "description": "French noun phrase, at most 10 words, no final punctuation",
                    },
                },
                "required": ["n", "abstract"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["abstracts"],
    "additionalProperties": False,
}


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


def default_out(inp: Path) -> Path:
    return inp.with_name(f"{inp.stem}_abstract.jsonl")


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


def numbered(rows: list[dict]) -> str:
    """The questions as the model sees them: 1-based, one per line."""
    return "\n".join(f"{i}. {r['text']}" for i, r in enumerate(rows, 1))


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
    prices: dict[str, tuple[float, float]]   # $/Mtok (input, output)
    batch_discount: float = 0.5
    supports_batch: bool = True
    install_hint: str = ""
    key_env: str = ""

    def price(self, model: str) -> tuple[float, float]:
        return self.prices.get(model, next(iter(self.prices.values())))

    # --- required of every provider -------------------------------------
    def request(self, rows: list[dict], model: str) -> dict:
        raise NotImplementedError

    def count_tokens(self, rows: list[dict], model: str) -> int | None:
        """Exact input-token count, or None if the API does not offer one."""
        return None

    def send(self, rows: list[dict], model: str) -> Reply:
        raise NotImplementedError

    # --- only needed when supports_batch --------------------------------
    def submit(self, chunks: list[list[dict]], model: str) -> str:
        raise NotImplementedError

    def wait(self, batch_id: str, poll: int) -> None:
        raise NotImplementedError

    def collect(self, batch_id: str) -> Iterator[tuple[str, Reply]]:
        raise NotImplementedError

    # --- shared helpers --------------------------------------------------
    def _missing(self) -> "SystemExit":
        return SystemExit(f"the {self.name} provider needs `{self.install_hint}`")

    def _no_key(self) -> "SystemExit":
        return SystemExit(f"no credentials for {self.name}: set {self.key_env}")


class AnthropicProvider(Provider):
    name = "anthropic"
    default_model = "claude-opus-5"
    prices = {"claude-opus-5": (5.00, 25.00),
              "claude-sonnet-5": (2.00, 10.00),
              "claude-haiku-4-5": (1.00, 5.00)}
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

    def request(self, rows, model):
        return {
            "model": model,
            "max_tokens": max_tokens_for(len(rows)),
            # cache_control only bites if SYSTEM exceeds the model's minimum
            # cacheable prefix (512-4096 tokens); below that it does nothing.
            "system": [{"type": "text", "text": SYSTEM,
                        "cache_control": {"type": "ephemeral"}}],
            # effort "low": this is extraction, not reasoning - the recommended
            # way to cut cost rather than disabling thinking outright
            "output_config": {"effort": "low",
                              "format": {"type": "json_schema", "schema": JSON_SCHEMA}},
            "messages": [{"role": "user", "content": numbered(rows)}],
        }

    def count_tokens(self, rows, model):
        return self.client().messages.count_tokens(
            model=model, system=SYSTEM,
            messages=self.request(rows, model)["messages"],
        ).input_tokens

    def _reply(self, message) -> Reply:
        if message.stop_reason == "refusal":
            cat = getattr(message.stop_details, "category", None)
            return Reply(None, message.model, f"refused ({cat})")
        if message.stop_reason == "max_tokens":
            return Reply(None, message.model, "hit max_tokens - lower --chunk")
        text = next((b.text for b in message.content if b.type == "text"), None)
        return Reply(text, message.model, None if text else "no text block")

    def send(self, rows, model):
        client = self.client()      # first, so a missing SDK reports itself properly
        import anthropic
        try:
            return self._reply(client.messages.create(**self.request(rows, model)))
        except anthropic.RateLimitError as exc:
            retry = int(exc.response.headers.get("retry-after", "60"))
            print(f"  rate limited, sleeping {retry}s", file=sys.stderr)
            time.sleep(retry)
            return Reply(None, model, "rate limited")
        except anthropic.APIStatusError as exc:
            return Reply(None, model, f"{exc.status_code} {exc.message}")

    def submit(self, chunks, model):
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request
        batch = self.client().messages.batches.create(requests=[
            Request(custom_id=f"chunk-{i}",
                    params=MessageCreateParamsNonStreaming(**self.request(c, model)))
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

    def __init__(self, *, name, base_url, key_env, default_model, prices,
                 schema_mode="json_schema", token_param="max_completion_tokens",
                 supports_batch=True):
        self.name = name
        self.base_url = base_url
        self.key_env = key_env
        self.default_model = default_model
        self.prices = prices
        self.schema_mode = schema_mode
        self.token_param = token_param
        self.supports_batch = supports_batch
        self.install_hint = "pip install openai"
        self._client = None

    def client(self):
        if self._client is None:
            import os
            try:
                import openai
            except ModuleNotFoundError:
                raise self._missing() from None
            key = os.environ.get(self.key_env)
            if not key:
                raise self._no_key()
            self._client = openai.OpenAI(api_key=key, base_url=self.base_url)
        return self._client

    def request(self, rows, model):
        if self.schema_mode == "json_schema":
            # strict schema enforcement; JSON_SCHEMA already satisfies its
            # requirements (every property required, additionalProperties false)
            fmt = {"type": "json_schema",
                   "json_schema": {"name": "abstracts", "strict": True,
                                   "schema": JSON_SCHEMA}}
        else:
            # JSON *mode*: valid JSON is guaranteed, the shape is not. The
            # schema is described in the prompt instead, and parse_chunk's
            # alignment check is what actually protects the output.
            fmt = {"type": "json_object"}
        return {
            "model": model,
            self.token_param: max_tokens_for(len(rows)),
            "response_format": fmt,
            # OpenAI-style APIs carry the system prompt as the first message
            # rather than as a separate top-level field
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": numbered(rows)}],
        }

    def _reply(self, completion) -> Reply:
        choice = completion.choices[0]
        model = getattr(completion, "model", self.default_model)
        if choice.finish_reason == "length":
            return Reply(None, model, "hit the token limit - lower --chunk")
        if choice.finish_reason == "content_filter":
            return Reply(None, model, "blocked by content filter")
        text = choice.message.content
        return Reply(text, model, None if text else "empty response")

    def send(self, rows, model):
        client = self.client()      # first, so a missing SDK reports itself properly
        import openai
        try:
            return self._reply(client.chat.completions.create(**self.request(rows, model)))
        except openai.RateLimitError:
            print("  rate limited, sleeping 60s", file=sys.stderr)
            time.sleep(60)
            return Reply(None, model, "rate limited")
        except openai.APIStatusError as exc:
            return Reply(None, model, f"{exc.status_code} {exc.message}")

    # -- batch: OpenAI's is file-based, unlike Anthropic's inline requests --
    def submit(self, chunks, model):
        import io
        lines = "\n".join(
            json.dumps({"custom_id": f"chunk-{i}", "method": "POST",
                        "url": "/v1/chat/completions",
                        "body": self.request(c, model)}, ensure_ascii=False)
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
                                 "hit the token limit - lower --chunk")
                continue
            yield cid, Reply(choice["message"].get("content"),
                             payload.get("model", self.default_model))


PROVIDERS: dict[str, Provider] = {
    "anthropic": AnthropicProvider(),
    "openai": OpenAICompatible(
        name="openai", base_url=None, key_env="OPENAI_API_KEY",
        default_model="gpt-5",
        # verify against https://openai.com/api/pricing before trusting `estimate`
        prices={"gpt-5": (1.25, 10.00)},
    ),
    "deepseek": OpenAICompatible(
        name="deepseek", base_url="https://api.deepseek.com",
        key_env="DEEPSEEK_API_KEY", default_model="deepseek-chat",
        prices={"deepseek-chat": (0.27, 1.10)},
        # DeepSeek offers JSON mode, not schema enforcement, and has no batch
        # API - both were true at the time of writing; check their docs.
        schema_mode="json_object", token_param="max_tokens", supports_batch=False,
    ),
}


# --------------------------------------------------------------------------
# shared: turn a Reply into records, or reject it
# --------------------------------------------------------------------------

def parse_chunk(reply: Reply, ids: list[str], label: str) -> list[dict]:
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
        items = json.loads(reply.text)["abstracts"]
        got = {int(it["n"]) for it in items}
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

    return [{"id": ids[int(it["n"]) - 1],
             "abstract": it["abstract"],
             "model": reply.model}
            for it in sorted(items, key=lambda it: int(it["n"]))]


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def _setup(args) -> tuple[Provider, Path, list[dict]]:
    prov = PROVIDERS[args.provider]
    args.model = args.model or prov.default_model
    out = args.output or default_out(args.input)
    return prov, out, pending(args.input, out, args.limit)


def cmd_estimate(args) -> None:
    prov, out, todo = _setup(args)
    if not todo:
        print("nothing to do - every id already has an abstract")
        return

    chunks = chunked(todo, args.chunk)
    # count a sample of whole chunks rather than all of them: count_tokens is
    # free but still one round trip per call, and these prompts are near-uniform
    sample = chunks[: min(5, len(chunks))]
    counts = [prov.count_tokens(c, args.model) for c in sample]
    if counts[0] is None:
        # no token-counting endpoint; French runs about 3.2 characters/token
        avg_in = sum(len(SYSTEM) + len(numbered(c)) for c in sample) / len(sample) / 3.2
        source = "estimated from characters"
    else:
        avg_in = sum(counts) / len(counts)
        source = f"counted over {len(sample)} chunk(s)"

    # ~25 tokens of answer per question, plus a fixed slice of thinking
    avg_out = 40 + 25 * args.chunk
    in_price, out_price = prov.price(args.model)
    batched = prov.supports_batch and not args.sync
    discount = prov.batch_discount if batched else 1.0
    cost = len(chunks) * discount * (avg_in * in_price + avg_out * out_price) / 1e6

    print(f"{len(todo)} question(s) still need an abstract")
    print(f"  provider         {prov.name}")
    print(f"  model            {args.model}")
    print(f"  chunk size       {args.chunk}  ->  {len(chunks)} request(s)")
    print(f"  avg input        {avg_in:.0f} tokens/request ({source})")
    print(f"  assumed output   {avg_out} tokens/request")
    print(f"  pricing          {'batch' if batched else 'standard'}")
    print(f"  estimated cost   ${cost:.2f}")
    print("\nOutput length is an assumption, not a measurement - run")
    print(f"`sync --limit {max(args.chunk * 2, 10)} --chunk {args.chunk}` first if the number matters.")


def cmd_run(args) -> None:
    prov, out, todo = _setup(args)
    if not todo:
        print("nothing to do - every id already has an abstract")
        return
    if not prov.supports_batch:
        sys.exit(f"{prov.name} has no batch API - use `sync` instead "
                 f"(and consider a larger --chunk to compensate)")

    chunks = chunked(todo, args.chunk)
    batch_id = prov.submit(chunks, args.model)

    # The ids each chunk covers have to survive to `fetch`, which may run in a
    # different process days later: the reply carries positions, not ids, and
    # recomputing the chunking from the input would drift as soon as anything
    # else is written to the output file.
    state = Path(f"{out}.batch")
    state.write_text(json.dumps({
        "provider": prov.name,
        "batch_id": batch_id,
        "chunks": {f"chunk-{i}": [str(r["id"]) for r in c] for i, c in enumerate(chunks)},
    }), encoding="utf-8")

    print(f"submitted {len(todo)} question(s) as {len(chunks)} request(s), batch {batch_id}")
    print(f"state saved to {state} - `fetch` resumes from it if this exits")

    prov.wait(batch_id, args.poll)
    _collect(prov, batch_id, out, state)


def cmd_fetch(args) -> None:
    out = args.output or default_out(args.input)
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
    batch_id = args.batch_id or saved["batch_id"]
    prov.wait(batch_id, args.poll)
    _collect(prov, batch_id, out, state)


def _collect(prov: Provider, batch_id: str, out: Path, state: Path) -> None:
    chunks: dict[str, list[str]] = json.loads(state.read_text(encoding="utf-8"))["chunks"]
    records, failed = [], 0
    for custom_id, reply in prov.collect(batch_id):
        ids = chunks.get(custom_id, [])
        got = parse_chunk(reply, ids, custom_id)
        records += got
        failed += len(ids) - len(got)

    append(out, records)
    state.unlink(missing_ok=True)
    print(f"\nwrote {len(records)} abstract(s) to {out}")
    if failed:
        print(f"{failed} question(s) got no abstract - run the same command "
              f"again to retry just those")


def cmd_sync(args) -> None:
    """One request at a time. For trying the prompt out, not for the corpus -
    except on a provider with no batch API, where it is the only option."""
    prov, out, todo = _setup(args)
    if not todo:
        print("nothing to do - every id already has an abstract")
        return

    chunks = chunked(todo, args.chunk)
    records = []
    for i, c in enumerate(chunks, 1):
        label = f"chunk {i}/{len(chunks)}"
        got = parse_chunk(prov.send(c, args.model), [str(r["id"]) for r in c], label)
        records += got
        # print each abstract against its source, which is the point of `sync`
        for rec, row in zip(got, c):
            print(f"[{label}] {rec['abstract']}")
            print(f"          {row['text'][:88]}")

    append(out, records)
    print(f"\nwrote {len(records)} abstract(s) to {out}")


def cmd_selftest(_args) -> None:
    """Offline: everything except the API call, for every provider."""
    import tempfile

    rows = [{"id": "aa", "text": "Sujet un."},
            {"id": "bb", "text": "Sujet deux."},
            {"id": "cc", "text": "Sujet trois."}]

    assert [len(c) for c in chunked(rows, 1)] == [1, 1, 1]
    assert [len(c) for c in chunked(rows, 2)] == [2, 1], "last chunk may be short"
    assert [len(c) for c in chunked(rows, 10)] == [3]
    assert numbered(rows) == "1. Sujet un.\n2. Sujet deux.\n3. Sujet trois."

    def parts(req: dict) -> tuple[str, str]:
        """(system text, user text), wherever this provider puts them."""
        user = next(m["content"] for m in req["messages"] if m["role"] == "user")
        system = next((m["content"] for m in req["messages"] if m["role"] == "system"), None)
        if system is None:                       # Anthropic: a top-level field
            system = "".join(b["text"] for b in req["system"])
        return system, user

    # every provider must build the same prompt without touching the network
    for name, prov in PROVIDERS.items():
        req = prov.request(rows, prov.default_model)
        assert req["model"] == prov.default_model, name
        system, user = parts(req)
        assert user == numbered(rows), f"{name} mangled the questions"
        assert system == SYSTEM, f"{name} lost the system prompt"
        cap = req.get("max_tokens") or req.get("max_completion_tokens")
        assert cap == max_tokens_for(3), f"{name} output cap"
    assert PROVIDERS["anthropic"].request(rows, "m")["messages"][0]["role"] == "user"
    assert PROVIDERS["openai"].request(rows, "m")["messages"][0]["role"] == "system", \
        "OpenAI-style APIs carry the system prompt inside messages"
    assert PROVIDERS["deepseek"].request(rows, "m")["response_format"] == {"type": "json_object"}
    assert PROVIDERS["openai"].request(rows, "m")["response_format"]["type"] == "json_schema"
    assert not PROVIDERS["deepseek"].supports_batch

    ids = [r["id"] for r in rows]
    ok = Reply(json.dumps({"abstracts": [{"n": 2, "abstract": "B"},   # out of order
                                         {"n": 1, "abstract": "A"},
                                         {"n": 3, "abstract": "C"}]}), "m")
    assert [(r["id"], r["abstract"]) for r in parse_chunk(ok, ids, "t")] == \
        [("aa", "A"), ("bb", "B"), ("cc", "C")], "n must map to id by position"

    # every misalignment must reject the whole chunk, not return part of it
    def payload(items):
        return Reply(json.dumps({"abstracts": items}), "m")

    assert parse_chunk(payload([{"n": 1, "abstract": "A"}, {"n": 2, "abstract": "B"}]),
                       ids, "t") == [], "a dropped item must void the chunk"
    assert parse_chunk(payload([{"n": 1, "abstract": "A"}] * 3), ids, "t") == [], \
        "repeated n must void the chunk"
    assert parse_chunk(payload([{"n": 0, "abstract": "A"}, {"n": 1, "abstract": "B"},
                                {"n": 2, "abstract": "C"}]), ids, "t") == [], \
        "0-based n must void the chunk"
    assert parse_chunk(Reply(None, "m", "refused"), ids, "t") == []
    # JSON mode can return valid JSON of the wrong shape - must not raise
    assert parse_chunk(Reply('{"results": []}', "m"), ids, "t") == [], "wrong key"
    assert parse_chunk(Reply("not json at all", "m"), ids, "t") == [], "not JSON"
    assert parse_chunk(Reply('{"abstracts": [{"abstract": "A"}]}', "m"), ids, "t") == [], \
        "missing n"

    with tempfile.TemporaryDirectory() as d:
        inp, out = Path(d) / "tache2.jsonl", Path(d) / "tache2_abstract.jsonl"
        inp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in [
            {"id": "1", "text": "Question une."},
            {"id": "2", "text": "Question deux."},
            {"id": "3", "text": ""},                 # no text -> never requested
        ]) + "\n", encoding="utf-8")

        assert default_out(inp) == out, default_out(inp)
        assert [r["id"] for r in pending(inp, out, None)] == ["1", "2"], "empty text must be skipped"

        append(out, [{"id": "1", "abstract": "Un sujet", "model": "m"}])
        assert [r["id"] for r in pending(inp, out, None)] == ["2"], "already-done ids must be skipped"
        assert read_jsonl(out)[0]["abstract"] == "Un sujet"

    print(f"selftest passed ({len(PROVIDERS)} providers)")


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
        p.add_argument("-m", "--model", default=None,
                       help="default: the provider's own default")
        p.add_argument("--limit", type=int, help="only the first N pending rows")
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
