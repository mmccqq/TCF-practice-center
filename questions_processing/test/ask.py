#!/usr/bin/env python3
"""
Send one prompt to the OpenAI API, write the reply to a file. That is all.

    python3 ask.py my_prompt.txt
    python3 ask.py my_prompt.txt questions.jsonl        # prompt, then the data
    python3 ask.py my_prompt.txt questions.jsonl --json # ask for strict JSON
    echo "say hi in French" | python3 ask.py            # prompt on stdin

llm.py is the production path - chunking, resume, four providers, alignment
checks. None of that helps while you are still deciding what to put in the
prompt, so this is the throwaway loop: edit the prompt, run, read the reply.

The reply goes to <prompt stem>_<HHMMSS>.txt next to the prompt, so successive
attempts never overwrite each other and you can diff two wordings afterwards.
Token counts go to stderr.

Needs:  pip install openai   and   export OPENAI_API_KEY=sk-...
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_MODEL = "gpt-5.6-luna"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt", nargs="?", type=Path,
                    help="file holding the prompt (default: read stdin)")
    ap.add_argument("attach", nargs="*", type=Path,
                    help="files appended to the prompt, e.g. the questions")
    ap.add_argument("-m", "--model", default=DEFAULT_MODEL)
    ap.add_argument("-o", "--output", type=Path, help="default: <prompt>_<time>.txt")
    ap.add_argument("--json", action="store_true",
                    help="require valid JSON back (response_format json_object)")
    ap.add_argument("--max-tokens", type=int, default=16000)
    ap.add_argument("--reasoning", default=None, metavar="LEVEL",
                    help="reasoning_effort, e.g. low - stops a thinking model "
                         "spending the whole budget before it answers")
    args = ap.parse_args()

    if args.prompt:
        if not args.prompt.exists():
            sys.exit(f"not found: {args.prompt}")
        text = args.prompt.read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    if not text.strip():
        sys.exit("empty prompt")

    for path in args.attach:
        if not path.exists():
            sys.exit(f"not found: {path}")
        text += "\n\n" + path.read_text(encoding="utf-8")

    try:
        import openai
    except ModuleNotFoundError:
        sys.exit("pip install openai")
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("set OPENAI_API_KEY")

    body = {
        "model": args.model,
        "max_completion_tokens": args.max_tokens,
        "messages": [{"role": "user", "content": text}],
    }
    if args.json:
        body["response_format"] = {"type": "json_object"}
    if args.reasoning:
        body["reasoning_effort"] = args.reasoning

    print(f"{len(text):,} characters -> {args.model}", file=sys.stderr)
    try:
        reply = openai.OpenAI().chat.completions.create(**body)
    except openai.APIStatusError as exc:
        sys.exit(f"{exc.status_code} {exc.message}")

    choice = reply.choices[0]
    out = choice.message.content or ""

    stamp = datetime.now().strftime("%H%M%S")
    stem = args.prompt.with_suffix("") if args.prompt else Path("reply")
    path = args.output or Path(f"{stem}_{stamp}.txt")
    path.write_text(out, encoding="utf-8")

    u = reply.usage
    print(f"finish_reason={choice.finish_reason}  "
          f"in={u.prompt_tokens:,}  out={u.completion_tokens:,}", file=sys.stderr)
    if choice.finish_reason == "length":
        print("! truncated - raise --max-tokens, or --reasoning low", file=sys.stderr)
    print(f"wrote {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
