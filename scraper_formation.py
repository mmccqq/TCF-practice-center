#!/usr/bin/env python3
"""
Scraper for the "Sujets d'actualites" monthly Tache 2 / Tache 3 pages on
https://www.formation-tcfcanada.com/epreuve/expression-orale/sujets-actualites

Two stages, same split as scraper.py and for the same reason: a wrong
extraction costs you a re-parse, not a re-crawl.

    pip install requests

    python3 scraper_formation.py crawl --raw-dir raw_formation --limit 3
    python3 scraper_formation.py parse --raw-dir raw_formation --out-dir questions_formation
    python3 scraper_formation.py selftest

`parse` reads raw_dir/_hub.html (saved by `crawl`) to look up each month's
real year/month - re-run `crawl` at least once if you ever delete it.

How the site actually works (reverse-engineered, not documented)
------------------------------------------------------------------
The site is a Next.js app. It server-renders no plain HTML for the data you
want; instead the page ships a React Server Component "flight" stream as a
series of

    self.__next_f.push([1, "<chunk>"])

script tags. Each <chunk> is a JSON-encoded string; concatenating them in
document order reconstructs one big text stream that is mostly-JSON with a
few Next.js-specific row markers mixed in (import refs, back-references,
raw text rows). We don't need a full flight-protocol parser: the two
payloads we care about are self-contained, well-formed JSON arrays sitting
behind a recognisable key, so we just locate the key and read a balanced
bracket span from there.

  * Hub page  -> "months":[{"name":"Août 2026","slug":"aot-2026",
                  "topics":80,"available":true,"year":2026}, ...]
  * Month page -> "parties":[{"id":2100,"jour":1,"date":"mars 2026",
                  "sujets":[{"id":17419,"tache":2,"title":"...","correction":
                  {"exemple":"..."}}, ...]}, ...]

Three important quirks:

  * The hub's "slug" field is NOT the deaccented month name you'd expect -
    for several 2026 months it's visibly mangled (e.g. "aot-2026" for Aout,
    "avril" with no year for Avril, and one janvier-2026 slug that's a
    garbled leftover of a staging URL). This is a bug on the site's own
    backend, but the mangled slugs are the real, working routes - fixing
    them up to the "correct" spelling produces 404s. So this "fetch slug"
    is used verbatim for the actual HTTP request and for the saved
    filename, but never shown in output - see canonical_slug().
  * Each party's own "date" field is similarly unreliable, but in the
    opposite direction: fixing it up isn't possible because there's no
    single format to fix it to. It's been seen as "Partie 5 - Août 2025",
    plain "Partie 5" with no date at all, "21 Mars 2026" (a full date,
    no partie number), mixed casing ("PARTIE 5"), and stray trailing
    spaces. So year/month are never read from "date" - they come from the
    hub's "months" list instead (matched by fetch slug), which is
    consistent. "jour" (the party's own sequence number) is used for the
    partie/combination number instead of parsing it out of "date" - it
    matches the "Partie N" number every time "date" happens to have one.
  * "title" holds the actual question text for both Tache 2 and Tache 3
    and is never paywalled/truncated. "correction.exemple" (model answer
    points) sometimes is a flight back-reference like "$38" instead of
    inline text; we don't chase those since the ask is the questions.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://www.formation-tcfcanada.com"

MONTHS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10,
    "novembre": 11, "decembre": 12,
}


def deaccent(s: str) -> str:
    """'Août' -> 'aout'. Used only to read the (reliable) display name -
    never to rebuild the (unreliable, sometimes-mangled) slug."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def month_num(name: str) -> int:
    """'Août 2026' -> 8. 0 if unrecognised."""
    word = name.rsplit(" ", 1)[0]
    return MONTHS.get(deaccent(word).lower(), 0)


HUB_PATH = "/epreuve/expression-orale/sujets-actualites"
HUB_URL = f"{BASE}{HUB_PATH}"
SOURCE = "formation"

# 2-digit source code used in Question.id, shared across this project's three
# scrapers so ids stay globally unique if their jsonl output is ever merged:
# 01 = this file, 02 = scraper_opal.py, 03 = scraper_reussir.py.
SOURCE_CODE = 1

USER_AGENT = (
    "TCFStudyScraper/1.0 (personal study project; contact: you@example.com)"
)

DELAY_SECONDS = 2.0
TIMEOUT = 20

PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,(".*?")\]\)', re.S)


@dataclass(frozen=True)
class MonthInfo:
    name: str          # "Août 2026" - display label, source of truth for the date
    slug: str          # "aot-2026" - opaque route param, use verbatim, never rebuild
    topics: int        # advertised subject count, used only to sanity-check parsing
    year: int


def canonical_slug(info: MonthInfo) -> str:
    """'aout-2026', 'janvier-2026' - clean form for output/ids, independent
    of the real fetch slug (info.slug), which can be mangled (e.g.
    "aot-2026", or the garbled janvier-2026 - see the module docstring)."""
    word = info.name.rsplit(" ", 1)[0]
    return f"{deaccent(word).lower()}-{info.year}"


@dataclass(frozen=True)
class Question:
    id: str            # source|year|month|tache|partie|sujet, e.g. "0120250820502"
    source: str
    month_slug: str     # canonical, e.g. "aout-2026" - not the (possibly mangled) fetch slug
    year: int
    month: int
    month_name: str
    tache: int
    jour: int           # the partie/combination number within the month
    sujet_id: int
    text: str
    source_url: str
    scraped_at: str


# --------------------------------------------------------------------------
# flight-stream helpers
# --------------------------------------------------------------------------

def flight_stream(html: str) -> str:
    """Concatenate every self.__next_f.push([1, "...json-string..."]) payload
    in document order. Each payload is itself a JSON string literal, so
    json.loads() on the captured group undoes the JS/JSON escaping in one
    step and hands back the raw stream text."""
    chunks = PUSH_RE.findall(html)
    if not chunks:
        raise ValueError("no __next_f.push chunks found - page layout changed")
    return "".join(json.loads(c) for c in chunks)


def extract_balanced(s: str, start: int) -> str:
    """Return s[start:end] where s[start] is '[' or '{' and end closes it,
    respecting (and not being fooled by brackets inside) JSON strings."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c in "[{":
                depth += 1
            elif c in "]}":
                depth -= 1
                if depth == 0:
                    return s[start : i + 1]
    raise ValueError("unbalanced brackets - payload was truncated")


def extract_json_array(stream: str, key: str) -> list:
    """Find `"<key>":[` in the flight stream and parse the array that follows."""
    m = re.search(rf'"{re.escape(key)}"\s*:\s*(\[)', stream)
    if not m:
        raise ValueError(f'key "{key}" not found in flight stream')
    return json.loads(extract_balanced(stream, m.start(1)))


# --------------------------------------------------------------------------
# stage 1: crawl
# --------------------------------------------------------------------------

class PoliteSession:
    """requests.Session + fixed delay between requests."""

    def __init__(self, delay: float = DELAY_SECONDS):
        self.delay = delay
        self._last_request = 0.0
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"}
        )

    def get(self, url: str) -> str:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request = time.monotonic()
        r = self.session.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text


def discover_months(html: str) -> list[MonthInfo]:
    stream = flight_stream(html)
    months = extract_json_array(stream, "months")
    return [
        MonthInfo(name=m["name"], slug=m["slug"], topics=m["topics"], year=m["year"])
        for m in months
        if m.get("available", True)
    ]


def crawl(raw_dir: Path, limit: int | None, delay: float, force: bool) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    sess = PoliteSession(delay=delay)

    index_path = raw_dir / "_hub.html"
    if force or not index_path.exists():
        print(f"GET {HUB_URL}")
        index_path.write_text(sess.get(HUB_URL), encoding="utf-8")
    months = discover_months(index_path.read_text(encoding="utf-8"))
    # newest first - sort by (year, month number), not month name (alphabetical
    # order would rank "Mars" ahead of "Aout")
    months.sort(key=lambda m: (m.year, month_num(m.name)), reverse=True)
    print(f"discovered {len(months)} month pages")

    for m in months[:limit]:
        dest = raw_dir / f"{m.slug}.html"
        if dest.exists() and not force:
            print(f"skip  {dest.name} (cached)")
            continue
        url = f"{HUB_URL}/{m.slug}"
        print(f"GET   {m.name:<14} {url}")
        try:
            dest.write_text(sess.get(url), encoding="utf-8")
        except Exception as exc:                      # noqa: BLE001
            print(f"  ! failed: {exc}", file=sys.stderr)


# --------------------------------------------------------------------------
# stage 2: parse
# --------------------------------------------------------------------------

def parse_month(html: str, info: MonthInfo, source_url: str) -> list[Question]:
    """Parse one month's flight stream into Questions.

    year/month/month_slug all come from `info` (the hub's "months" entry for
    this month, looked up by parse_all), never from each party's own "date"
    text - see the module docstring for why "date" can't be trusted.
    """
    stream = flight_stream(html)
    parties = extract_json_array(stream, "parties")

    year = info.year
    month = month_num(info.name)
    month_name = deaccent(info.name.rsplit(" ", 1)[0]).lower()
    slug = canonical_slug(info)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    out: list[Question] = []
    for party in parties:
        jour = party.get("jour", 0)
        seq = {2: 0, 3: 0}          # per-partie, per-tache running count -> id's last 2 digits
        for sujet in party.get("sujets", []):
            tache = sujet.get("tache")
            if tache not in (2, 3):
                continue                                # ignore anything unexpected
            text = (sujet.get("title") or "").strip()
            if not text:
                continue
            seq[tache] += 1
            out.append(Question(
                id=f"{SOURCE_CODE:02d}{year:04d}{month:02d}{tache:d}{jour:02d}{seq[tache]:02d}",
                source=SOURCE,
                month_slug=slug,
                year=year,
                month=month,
                month_name=month_name,
                tache=tache,
                jour=jour,
                sujet_id=sujet.get("id", 0),
                text=text,
                source_url=source_url,
                scraped_at=now,
            ))
    return out


def parse_all(raw_dir: Path, out_dir: Path) -> None:
    hub_path = raw_dir / "_hub.html"
    if not hub_path.exists():
        sys.exit(f"{hub_path} not found - run `crawl` at least once first "
                 f"(parse needs it to look up each month's real year/month)")
    months_by_fetch_slug = {
        m.slug: m for m in discover_months(hub_path.read_text(encoding="utf-8"))
    }

    files = sorted(p for p in raw_dir.glob("*.html") if not p.name.startswith("_"))
    if not files:
        sys.exit(f"no saved pages in {raw_dir} - run `crawl` first")

    out_dir.mkdir(parents=True, exist_ok=True)
    tache2_path = out_dir / "tache2.jsonl"
    tache3_path = out_dir / "tache3.jsonl"

    counts = {2: 0, 3: 0}
    with tache2_path.open("w", encoding="utf-8") as f2, \
         tache3_path.open("w", encoding="utf-8") as f3:
        for path in files:
            fetch_slug = path.stem
            info = months_by_fetch_slug.get(fetch_slug)
            if info is None:
                print(f"  ! {fetch_slug}: not listed on the cached hub page - "
                      f"re-run `crawl` to refresh _hub.html, skipping", file=sys.stderr)
                continue
            url = f"{HUB_URL}/{fetch_slug}"
            try:
                questions = parse_month(path.read_text(encoding="utf-8"), info, url)
            except ValueError as exc:
                print(f"  ! {fetch_slug}: {exc}", file=sys.stderr)
                continue
            if not questions:
                print(f"  ! {fetch_slug}: 0 questions - layout may differ", file=sys.stderr)
            n2 = n3 = 0
            for q in questions:
                fh = f2 if q.tache == 2 else f3
                fh.write(json.dumps(asdict(q), ensure_ascii=False) + "\n")
                if q.tache == 2:
                    n2 += 1
                else:
                    n3 += 1
            counts[2] += n2
            counts[3] += n3
            print(f"{canonical_slug(info):24} tache2={n2:3}  tache3={n3:3}")

    print(f"\ntache 2: {counts[2]} -> {tache2_path}")
    print(f"tache 3: {counts[3]} -> {tache3_path}")


# --------------------------------------------------------------------------
# selftest (no network - validates extraction on a synthetic flight stream)
# --------------------------------------------------------------------------

def _fixture_page(payload_key: str, payload: object) -> str:
    """Wrap a JSON payload the way Next.js would: as a JSON-string-escaped
    self.__next_f.push chunk, split across two pushes to mimic real pages."""
    inner = json.dumps({payload_key: payload}, ensure_ascii=False, separators=(",", ":"))
    # simulate the stream being split mid-object across two push() calls,
    # since real pages never hand us the payload in one contiguous chunk
    mid = len(inner) // 2
    part_a, part_b = inner[:mid], inner[mid:]
    chunk_a = json.dumps(f'0:["$","div",null,{{}}]\n1:{part_a}')
    chunk_b = json.dumps(part_b)
    return (
        "<html><body>"
        f"<script>self.__next_f.push([1,{chunk_a}])</script>"
        f"<script>self.__next_f.push([1,{chunk_b}])</script>"
        "</body></html>"
    )


def selftest() -> None:
    hub_html = _fixture_page("months", [
        {"name": "Août 2026", "slug": "aot-2026", "topics": 2, "available": True, "year": 2026},
        {"name": "Mars 2026", "slug": "mars-2026", "topics": 4, "available": False, "year": 2026},
    ])
    months = discover_months(hub_html)
    assert len(months) == 1 and months[0].slug == "aot-2026", \
        "unavailable months should be dropped, slug must survive verbatim"
    info = months[0]

    # party 1's "date" has no year at all ("Partie 1"), party 2's does
    # ("Partie 2 - Août 2026") - year/month/month_slug must come out
    # identical either way, since both are read from `info`, never "date".
    month_html = _fixture_page("parties", [
        {"id": 1, "jour": 1, "date": "Partie 1", "sujets": [
            {"id": 101, "tache": 2, "title": "Question tache 2 numero un.",
             "correction": {"exemple": "$99"}},
            {"id": 102, "tache": 2, "title": "Question tache 2 numero deux.",
             "correction": {"exemple": ""}},
            {"id": 103, "tache": 3, "title": "Question tache 3 numero un ?"},
            {"id": 104, "tache": 9, "title": "Should be ignored - unknown tache."},
        ]},
        {"id": 2, "jour": 2, "date": "Partie 2 - Août 2026", "sujets": [
            {"id": 105, "tache": 3, "title": "Question tache 3, partie deux."},
        ]},
    ])
    qs = parse_month(month_html, info, "https://example.test/aot-2026")
    assert [q.tache for q in qs] == [2, 2, 3, 3], "unknown tache should be dropped"
    assert qs[0].text == "Question tache 2 numero un."
    assert qs[2].sujet_id == 103
    assert all(q.year == 2026 and q.month == 8 and q.month_name == "aout" for q in qs), \
        "year/month must come from the hub, not each party's inconsistent 'date' text"
    assert all(q.month_slug == "aout-2026" for q in qs), \
        "month_slug must be the canonical form, not the mangled fetch slug 'aot-2026'"
    assert all(q.source == SOURCE for q in qs), "source field not stamped"

    ids = [q.id for q in qs]
    assert ids == ["0120260820101", "0120260820102",
                   "0120260830101", "0120260830201"], ids
    # The tache digit is what makes these unique: ids[0] and ids[2] share the
    # same (partie 1, sequence 1) slot and would collide without it.
    assert len(set(ids)) == len(ids), "ids must be unique across taches"

    print("selftest passed:", len(months), "month(s) discovered,", len(qs), "question(s) parsed")


# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("crawl", help="download raw HTML")
    c.add_argument("--raw-dir", type=Path, default=Path("raw_formation"))
    c.add_argument("--limit", type=int, default=None, help="only the N newest months")
    c.add_argument("--delay", type=float, default=DELAY_SECONDS)
    c.add_argument("--force", action="store_true", help="re-download cached pages")

    p = sub.add_parser("parse", help="extract questions from saved HTML")
    p.add_argument("--raw-dir", type=Path, default=Path("raw_formation"))
    p.add_argument("--out-dir", type=Path, default=Path("questions_formation"),
                   help="directory to write tache2.jsonl and tache3.jsonl into")

    sub.add_parser("selftest", help="run the offline parser test")

    args = ap.parse_args()
    if args.cmd == "crawl":
        crawl(args.raw_dir, args.limit, args.delay, args.force)
    elif args.cmd == "parse":
        parse_all(args.raw_dir, args.out_dir)
    else:
        selftest()


if __name__ == "__main__":
    main()
