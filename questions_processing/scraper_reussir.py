#!/usr/bin/env python3
"""
Scraper for the reussir-tcfcanada.com 'expression orale' monthly subject pages.

Two stages, deliberately separated:
  1. crawl  -> downloads raw HTML to disk (network, slow, polite)
  2. parse  -> reads the saved HTML and emits Tache 2 / Tache 3 JSONL (offline, fast)

    pip install requests beautifulsoup4 lxml

    python3 scraper_reussir.py crawl --raw-dir raw_reussir --limit 3
    python3 scraper_reussir.py parse --raw-dir raw_reussir --out-dir questions_reussir
    python3 scraper_reussir.py selftest
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
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

BASE = "https://reussir-tcfcanada.com"
INDEX_URL = f"{BASE}/expression-orale/"
SOURCE = "reussir"
# 2-digit source code used in Question.id, shared across this project's three
# scrapers so ids stay globally unique if their jsonl output is ever merged:
# 01 = scraper_formation.py, 02 = scraper_opal.py, 03 = this file.
SOURCE_CODE = 3

USER_AGENT = "TCFStudyScraper/1.0 (personal study project; contact: you@example.com)"
DELAY_SECONDS = 3.0
RETRIES = 3
TIMEOUT = 20

MONTHS = {
    "janvier": 1, "junvier": 1,   # the site has a typo in the 2022 slug
    "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10,
    "novembre": 11, "decembre": 12,
}

MONTH_SLUG_RE = re.compile(r"/([a-z]+)-(\d{4})-expression-orale/?$")

RE_TACHE = re.compile(r"^t[âa]che\s*(\d+)$", re.I)
RE_PARTIE = re.compile(r"^partie\s*(\d+)$", re.I)
RE_SUJET = re.compile(r"^sujet\s*(\d+)$", re.I)

# Once we hit the share/footer block, everything after it is boilerplate.
RE_STOP = re.compile(r"pour partager les sujets", re.I)


@dataclass(frozen=True)
class Question:
    id: str            # source|year|month|tache|partie|sujet, e.g. "0320260820401"
    source: str
    month_slug: str
    year: int
    month: int
    tache: int
    partie: int
    sujet: int
    text: str
    source_url: str
    scraped_at: str


def deaccent(s: str) -> str:
    """'Février' -> 'fevrier'. Needed because slugs drop accents inconsistently."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def clean(s: str) -> str:
    """Collapse whitespace and normalise the non-breaking spaces WordPress emits."""
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def slug_of(url: str) -> str | None:
    m = MONTH_SLUG_RE.search(deaccent(urlparse(url).path.lower()))
    return f"{m.group(1)}-{m.group(2)}" if m else None


# --------------------------------------------------------------------------
# stage 1: crawl
# --------------------------------------------------------------------------

def polite_get(session: requests.Session, url: str, delay: float) -> str:
    """GET with a fixed politeness delay and a couple of retries on failure."""
    for attempt in range(1, RETRIES + 1):
        try:
            r = session.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        except requests.RequestException as exc:
            if attempt == RETRIES:
                raise
            wait = delay * attempt
            print(f"  ! {exc}; retry {attempt}/{RETRIES} in {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
        finally:
            time.sleep(delay)
    raise RuntimeError("unreachable")


def discover_month_urls(html: str) -> list[str]:
    """Pull the month page URLs out of the index page, newest first."""
    soup = BeautifulSoup(html, "lxml")
    seen: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        url = urljoin(BASE, a["href"])
        if urlparse(url).netloc != urlparse(BASE).netloc:
            continue
        slug = slug_of(url)
        if slug and slug not in seen:
            seen[slug] = url

    def sort_key(slug: str) -> tuple[int, int]:
        name, year = slug.rsplit("-", 1)
        return (int(year), MONTHS.get(name, 0))

    return [seen[s] for s in sorted(seen, key=sort_key, reverse=True)]


def crawl(raw_dir: Path, limit: int | None, delay: float, force: bool) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"})

    index_path = raw_dir / "_index.html"
    if force or not index_path.exists():
        print(f"GET {INDEX_URL}")
        index_path.write_text(polite_get(session, INDEX_URL, delay), encoding="utf-8")
    urls = discover_month_urls(index_path.read_text(encoding="utf-8"))
    print(f"discovered {len(urls)} month pages")

    for url in urls[:limit]:
        dest = raw_dir / f"{slug_of(url)}.html"
        if dest.exists() and not force:
            print(f"skip  {dest.name} (cached)")
            continue
        print(f"GET   {url}")
        try:
            dest.write_text(polite_get(session, url, delay), encoding="utf-8")
        except Exception as exc:                      # noqa: BLE001
            print(f"  ! failed: {exc}", file=sys.stderr)


# --------------------------------------------------------------------------
# stage 2: parse
# --------------------------------------------------------------------------

def bold_group(node: Tag) -> tuple[str, list[Tag]]:
    """Text of the whole bold run `node` belongs to, plus the tags it covers.

    reussir's editor regularly breaks one question across several <strong>
    tags inside a single <p>: at a <br> line wrap, or wherever a colour <span>
    was applied mid-sentence. Reading each <strong> on its own truncates the
    question at the first break and then discards the tail, because the
    remainder ("tourisme qui vous accueille.") is too short to look like a
    subject. So the run is read as one unit.

    Joining rule, which is the only one that gets both real shapes right:
    pieces are concatenated with no separator, and each <br> contributes one
    space. The document's own trailing spaces do the rest.

        "Je suis " + "un(e" + ") ami(e)"        -> "Je suis un(e) ami(e)"
        "...l'office du" + <br> + "tourisme..." -> "...l'office du tourisme..."

    Anything not inside a <strong> is skipped, so a "Sujet 2" label sharing
    the paragraph cannot leak into the text. If such a marker is present the
    grouping is abandoned altogether and the caller falls back to this one
    tag - a paragraph holding two separate subjects must not be fused.
    """
    block = node.find_parent(["p", "li", "td"])
    if block is None:
        return clean(node.get_text("")), [node]

    members = [t for t in block.find_all(["strong", "b"])
               if not t.find_parent(["strong", "b"])]
    if len(members) < 2 or not any(m is node for m in members):
        return clean(node.get_text("")), [node]

    parts: list[str] = []
    for el in block.descendants:
        if isinstance(el, Tag):
            if el.name == "br":
                parts.append(" ")
        elif el.find_parent(["strong", "b"]) is not None:
            parts.append(str(el))
        elif clean(str(el)) and (RE_TACHE.match(clean(str(el)))
                                 or RE_PARTIE.match(clean(str(el)))
                                 or RE_SUJET.match(clean(str(el)))):
            return clean(node.get_text("")), [node]     # two subjects in one <p>

    return clean("".join(parts)), members


def parse_month(html: str, slug: str, source_url: str) -> list[Question]:
    """
    Walk the document in reading order and run a small state machine.

    The page is built with Elementor, so the div nesting is arbitrary and
    unstable across months - CSS selectors targeting generated class names
    would break constantly. What IS stable is the visual grammar:

        "Tâche 2" ... "Partie 4" ... "Sujet 1" ... <strong>the question</strong>

    So we ignore structure entirely and key off that grammar instead.
    """
    soup = BeautifulSoup(html, "lxml")
    for junk in soup(["script", "style", "nav", "footer", "header"]):
        junk.decompose()

    name, year = slug.rsplit("-", 1)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    tache = partie = sujet = None
    out: list[Question] = []
    seen: set[tuple] = set()
    consumed: set[int] = set()   # ids of <strong> tags already emitted

    root = soup.body or soup
    for node in root.descendants:
        if isinstance(node, Tag) and node.name in ("strong", "b"):
            if id(node) in consumed:
                continue
            # mark nested bold tags so we don't emit the same text twice
            for inner in node.find_all(["strong", "b"]):
                consumed.add(id(inner))
            # ...and the siblings absorbed into this one, for the same reason:
            # a question split across several <strong> tags is one question
            text, group = bold_group(node)
            for member in group:
                consumed.add(id(member))
                for inner in member.find_all(["strong", "b"]):
                    consumed.add(id(inner))
            # a real subject is a sentence; short bold runs are labels/emphasis
            if tache in (2, 3) and sujet is not None and len(text) > 40:
                key = (tache, partie, sujet, text)
                if key not in seen:
                    seen.add(key)
                    partie_num = partie or 0
                    year_num = int(year)
                    month_num = MONTHS.get(name, 0)
                    # "Sujet N" already resets per Tache/Partie change (see
                    # the RE_TACHE/RE_PARTIE handling below), so `sujet` is
                    # already exactly the id's per-(tache,partie) sequence
                    # number - no separate counter needed.
                    out.append(Question(
                        id=f"{SOURCE_CODE:02d}{year_num:04d}{month_num:02d}{tache:d}{partie_num:02d}{sujet:02d}",
                        source=SOURCE,
                        month_slug=slug,
                        year=year_num,
                        month=month_num,
                        tache=tache,
                        partie=partie_num,
                        sujet=sujet,
                        text=text,
                        source_url=source_url,
                        scraped_at=now,
                    ))
                sujet = None      # consume it; one question per Sujet marker
            continue

        if isinstance(node, NavigableString):
            text = clean(str(node))
            if not text:
                continue
            if RE_STOP.search(text):
                break
            if m := RE_TACHE.match(text):
                tache, partie, sujet = int(m.group(1)), None, None
            elif m := RE_PARTIE.match(text):
                partie, sujet = int(m.group(1)), None
            elif m := RE_SUJET.match(text):
                sujet = int(m.group(1))

    return out


def parse_all(raw_dir: Path, out_dir: Path) -> None:
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
            slug = path.stem
            url = f"{BASE}/{slug}-expression-orale/"
            questions = parse_month(path.read_text(encoding="utf-8"), slug, url)
            if not questions:
                print(f"  ! {slug}: 0 questions - layout may differ", file=sys.stderr)
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
            print(f"{slug:24} tache2={n2:3}  tache3={n3:3}")

    print(f"\ntache 2: {counts[2]} -> {tache2_path}")
    print(f"tache 3: {counts[3]} -> {tache3_path}")


# --------------------------------------------------------------------------
# selftest (no network - validates the state machine on a synthetic page)
# --------------------------------------------------------------------------

FIXTURE = """
<html><body>
<div class="elementor-widget"><h1>Août 2026</h1></div>
<div><div><h2>Tâche 2</h2></div></div>
<div><span>Partie 4</span></div>
<div><p>Sujet 1</p><p><strong>Je suis un(e) ami(e). Vous me demandez des idées
   pour préparer ce repas (endroit, menu, coût, etc.).</strong></p></div>
<div><p>Sujet 2</p><p><strong>Je suis un(e) colocataire. Vous me demandez des
   renseignements sur cette visite (durée, ambiance, etc.).</strong></p></div>
<div><span>Partie 3</span></div>
<div><p>Sujet 1</p><p><strong>Je suis un(e) collègue. Vous me posez des questions
   pour obtenir des informations (itinéraire, horaires, etc.).</strong></p></div>
<div><span>Partie 5</span></div>
<!-- the editor's two real ways of splitting one question across <strong> tags:
     a <br> line wrap (needs a space) and a mid-word colour span (needs none) -->
<div><p>Sujet 1</p><p><strong>Je suis l'employé(e) de l'office du</strong><br /><strong>tourisme
   qui vous accueille. Posez-moi des questions (prix, horaires, etc.).</strong></p></div>
<div><p>Sujet 2</p><p><strong>Je suis votre voisin(e). V</strong><span><strong>ous me posez
   des questions sur le quartier (services, magasins, etc.)</strong></span><strong>.</strong></p></div>
<h2>Tâche 3</h2>
<div><span>Partie 1</span></div>
<div><p>Sujet 1</p><p><strong>Selon vous, est-il important qu'une entreprise
   privilégie le bien-être de ses salariés ?</strong></p></div>
<h2>Pour partager les sujets de votre session:</h2>
<p>Sujet 9</p><p><strong>Ceci est du contenu de pied de page qui ne doit jamais
   être extrait par le parseur.</strong></p>
</body></html>
"""


def selftest() -> None:
    qs = parse_month(FIXTURE, "aout-2026", "https://example.test/")
    assert [q.tache for q in qs] == [2, 2, 2, 2, 2, 3], "state machine produced the wrong hierarchy"
    assert all("pied de page" not in q.text for q in qs), "footer leaked in"

    # A question split across several <strong> tags is one question: the tail
    # must survive, and the two joining rules must not contaminate each other.
    by_text = {q.id: q.text for q in qs}
    br_split = by_text["0320260820501"]
    assert br_split.endswith("(prix, horaires, etc.)."), br_split
    assert "du tourisme" in br_split, f"<br> must join with a space: {br_split}"
    span_split = by_text["0320260820502"]
    assert "voisin(e). Vous me posez" in span_split, \
        f"a mid-word split must join with nothing: {span_split}"
    assert span_split.endswith("(services, magasins, etc.)."), \
        f"trailing punctuation in its own <strong> was dropped: {span_split}"
    assert qs[0].month == 8 and qs[0].year == 2026
    assert all(q.source == SOURCE for q in qs), "source field not stamped"
    assert qs[0].id == "0320260820401", qs[0].id
    # Real data commonly reuses the same partie/sujet numbers under both
    # Tache 2 and Tache 3; the tache digit is what keeps those ids distinct.
    assert len({q.id for q in qs}) == len(qs), "ids must be unique across taches"
    print("selftest passed:", len(qs), "questions, tache split OK")


# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("crawl", help="download raw HTML")
    c.add_argument("--raw-dir", type=Path, default=Path("raw_reussir"))
    c.add_argument("--limit", type=int, default=None, help="only the N newest months")
    c.add_argument("--delay", type=float, default=DELAY_SECONDS)
    c.add_argument("--force", action="store_true", help="re-download cached pages")

    p = sub.add_parser("parse", help="extract questions from saved HTML")
    p.add_argument("--raw-dir", type=Path, default=Path("raw_reussir"))
    p.add_argument("--out-dir", type=Path, default=Path("questions_reussir"),
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
