#!/usr/bin/env python3
"""
Crawl and parse the monthly "Sujet d'actualité" pages at
https://tcfcanada.opal-ca.com/expression-orale/

Two stages, deliberately separated:
  1. crawl  -> downloads raw HTML to disk (network, slow, polite)
  2. parse  -> reads the saved HTML and emits Tache 2 / Tache 3 JSONL (offline, fast)

Notes
-----
* Despite appearances, the "sujet" pages themselves are public - no login
  needed. Only the "*-correction" (model answer) pages sit behind a Simple
  WordPress Membership (SWPM) login, so --cookie-file / OPAL_USER+OPAL_PASS
  are only needed if you pass --corrections. Only download content you are
  entitled to access.
* --discover hub (the default) reads the real month->URL list straight out
  of the hub page - it's not plain <a href> markup, it's a JSON blob in a
  data-attributes="..." attribute (see _actualite_items_from_widget), and
  it's the authoritative source for each month's actual slug, abbreviated
  or not (e.g. "avr-2024", not the "avril-2024" you'd expect). Only fall
  back to --discover slugs (which guesses every known spelling per month
  and lets 404s sort it out) if the hub page's layout changes again or you
  need a year the hub no longer lists.
* Re-runs are cheap: the manifest stores ETag / Last-Modified per URL and the
  script issues conditional GETs, so an unchanged month returns 304 and is skipped.

Usage
-----
    pip install requests beautifulsoup4 lxml

    python3 scraper_opal.py crawl --raw-dir raw_opal
    python3 scraper_opal.py parse --raw-dir raw_opal --out-dir questions_opal
    python3 scraper_opal.py selftest

    # corrections need a login
    export OPAL_USER=... OPAL_PASS=...
    python3 scraper_opal.py crawl --raw-dir raw_opal --corrections

    # fallback: guess slugs for years the hub page no longer lists
    python3 scraper_opal.py crawl --discover slugs --years 2022 2023 --raw-dir raw_opal
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = "https://tcfcanada.opal-ca.com"
HUB_URL = f"{BASE}/expression-orale/"
LOGIN_URL = f"{BASE}/index.php/membership-login/"
SOURCE = "opal"
# 2-digit source code used in Question.id, shared across this project's three
# scrapers so ids stay globally unique if their jsonl output is ever merged:
# 01 = scraper_formation.py, 02 = this file, 03 = scraper_reussir.py.
SOURCE_CODE = 2
USER_AGENT = "sujets-actualite-archiver/1.0 (personal study archive; contact: you@example.com)"

# Slug spellings actually used on the site (unaccented, sometimes abbreviated).
MONTHS: dict[str, int] = {
    "janvier": 1, "janv": 1, "jan": 1,
    "fevrier": 2, "fev": 2, "fevr": 2,
    "mars": 3,
    "avril": 4, "avr": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7, "juil": 7, "jui": 7,
    "aout": 8,
    "septembre": 9, "sept": 9, "sep": 9,
    "octobre": 10, "oct": 10,
    "novembre": 11, "nov": 11,
    "decembre": 12, "dec": 12,
}

# Reverse index: 4 -> ["avril", "avr"]. Needed because the site does not use
# one consistent spelling per month across years - e.g. avril-2024 is a 404,
# only the abbreviated avr-2024 exists, while avril-2025 and avril-2026 are
# the full spelling and avr-2025/avr-2026 404. There is no way to predict
# which spelling a given year picked, so discover_from_slugs() tries them all.
SPELLINGS: dict[int, list[str]] = {}
for _name, _num in MONTHS.items():
    SPELLINGS.setdefault(_num, []).append(_name)

MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))
SLUG_RE = re.compile(
    rf"/expression-orale-(?P<month>{MONTH_ALT})-(?P<year>20\d{{2}})(?P<suffix>-correction)?/?$",
    re.IGNORECASE,
)

log = logging.getLogger("opal")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def deaccent(text: str) -> str:
    """Lowercase and strip diacritics so 'Sujet d'Actualité' == 'sujet d actualite'."""
    stripped = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", stripped.lower()).strip()


def section_pattern(section: str) -> re.Pattern[str]:
    """'sujet d'actualite' also matches 'SUJETS D'ACTUALITÉ', 'Sujets  d actualites', ..."""
    tokens = deaccent(section).split()
    return re.compile(r"\s*".join(rf"{re.escape(t)}s?" for t in tokens))


def clean(s: str) -> str:
    """Collapse whitespace and normalise the non-breaking spaces WordPress emits."""
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def build_session(cookie_file: str | None, timeout: int) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    })
    retry = Retry(
        total=5,
        connect=3,
        read=3,
        backoff_factor=1.5,                       # 0s, 1.5s, 3s, 6s, 12s
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD", "POST"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=4)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.request = _with_timeout(session.request, timeout)  # type: ignore[method-assign]

    if cookie_file:
        jar = MozillaCookieJar(cookie_file)
        jar.load(ignore_discard=True, ignore_expires=True)
        session.cookies.update(jar)
        log.info("Loaded %d cookies from %s", len(jar), cookie_file)
    return session


def _with_timeout(request_fn, timeout: int):
    def wrapper(method, url, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return request_fn(method, url, **kwargs)
    return wrapper


def swpm_login(session: requests.Session, username: str, password: str) -> bool:
    """Log in through the Simple WordPress Membership form and keep the cookie."""
    page = session.get(LOGIN_URL)
    page.raise_for_status()
    soup = BeautifulSoup(page.text, "html.parser")

    form = soup.find("form", attrs={"name": "swpm-login-form"}) or soup.find(
        "form", action=re.compile(r"membership-login|swpm", re.I)
    )
    payload = {"swpm_user_name": username, "swpm_password": password, "swpm_login": "Log In"}
    action = LOGIN_URL
    if form:
        action = urljoin(LOGIN_URL, form.get("action") or LOGIN_URL)
        for hidden in form.find_all("input", attrs={"type": "hidden"}):
            if hidden.get("name"):
                payload.setdefault(hidden["name"], hidden.get("value", ""))

    resp = session.post(action, data=payload, allow_redirects=True)
    resp.raise_for_status()
    ok = any(c.name.startswith("swpm_") or "wordpress_logged_in" in c.name
             for c in session.cookies)
    log.info("Login %s", "succeeded" if ok else "did NOT set a session cookie")
    return ok


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MonthPage:
    source: str
    url: str
    year: int
    month: int
    label: str
    kind: str  # "sujet" | "correction"

    @property
    def stem(self) -> str:
        return f"{self.year}-{self.month:02d}_{self.kind}"


def _classify(url: str, label: str) -> MonthPage | None:
    m = SLUG_RE.search(urlparse(url).path)
    if not m:
        return None
    return MonthPage(
        source=SOURCE,
        url=urljoin(BASE, url),
        year=int(m.group("year")),
        month=MONTHS[m.group("month").lower()],
        label=" ".join(label.split()) or m.group(0).strip("/"),
        kind="correction" if m.group("suffix") else "sujet",
    )


def _actualite_items_from_widget(html_text: str, section: str) -> list[dict] | None:
    """Pull the real month->URL list straight out of the hub page's data.

    The "Sujets d'actualite" block isn't rendered as plain <a href> markup at
    all - it's a custom block whose items are JS-templated client-side from a
    JSON blob sitting in a `data-attributes="..."` HTML attribute:

        data-attributes='{"title":"SUJETS D&#039;ACTUALITE","lists":[
            {"text":"AVRIL 2024","link":"https://.../expression-orale-avr-2024/"},
            ...]}'

    That JSON is the authoritative source for each month's real slug - it's
    literally where "avr-2024" (not "avril-2024") comes from - so we should
    read it directly instead of guessing spellings (see discover_from_slugs)
    or scanning for anchors that don't exist on this page. Unpublished months
    are listed here with an empty "link" and the caller skips them.

    Returns None (not []) if the widget isn't found, so the caller can tell
    "found the block, it's just empty" apart from "block isn't there at all"
    and fall back accordingly.
    """
    pattern = section_pattern(section)
    for m in re.finditer(r'data-attributes=(["\'])(.*?)\1', html_text, re.S):
        try:
            data = json.loads(html.unescape(m.group(2)))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and pattern.search(deaccent(data.get("title", ""))):
            return data.get("lists", [])
    return None


def discover_from_hub(session: requests.Session, section: str) -> list[MonthPage]:
    """Find the target section on the hub page and return its month links."""
    resp = session.get(HUB_URL)
    resp.raise_for_status()

    items = _actualite_items_from_widget(resp.text, section)
    if items is not None:
        log.info("Found section %r as a data-attributes widget with %d entries",
                  section, len(items))
        pages = {}
        for item in items:
            link = (item.get("link") or "").strip()
            if not link:
                continue                              # month not published yet
            if p := _classify(link, item.get("text", "")):
                pages[p.url] = p
        return sorted(pages.values(), key=lambda p: (p.year, p.month, p.kind))

    # Fall back to a plain anchor scan, in case the site ever goes back to
    # rendering this section as ordinary links.
    soup = BeautifulSoup(resp.text, "html.parser")
    pattern = section_pattern(section)
    heading = next(
        (h for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "strong"])
         if pattern.search(deaccent(h.get_text()))),
        None,
    )

    anchors: list = []
    if heading is not None:
        level = heading.name
        for node in heading.find_all_next():
            # stop at the next heading of the same or higher rank
            if node.name and re.fullmatch(r"h[1-6]", node.name) and node.name <= level:
                break
            if node.name == "a" and node.get("href"):
                anchors.append(node)
        log.info("Found section %r with %d candidate links", heading.get_text(strip=True), len(anchors))
    else:
        log.warning("Section %r not found (paywalled, renamed, or JS-rendered) - scanning whole page",
                     section)
        anchors = soup.find_all("a", href=True)

    pages = {p.url: p for a in anchors if (p := _classify(a["href"], a.get_text())) }
    return sorted(pages.values(), key=lambda p: (p.year, p.month, p.kind))


def discover_from_slugs(years: list[int], corrections: bool) -> list[MonthPage]:
    """Build candidate URLs from the site's slug convention; 404s are skipped later.

    We queue every known spelling of each month (see SPELLINGS) rather than
    picking one canonical name, since the real spelling varies by year in a
    way that can't be predicted up front. download()'s 404 handling silently
    drops whichever candidates don't exist, and since all candidates for a
    given year/month land on the same output filename (MonthPage.stem doesn't
    encode the spelling), only the one that actually 200s gets saved.
    """
    kinds = ["", "-correction"] if corrections else [""]
    out = []
    for year in years:
        for month in range(1, 13):
            for name in SPELLINGS[month]:
                for suffix in kinds:
                    url = f"{BASE}/expression-orale-{name}-{year}{suffix}/"
                    page = _classify(url, f"Expression Orale {name.upper()} {year}")
                    if page:
                        out.append(page)
    return out


# --------------------------------------------------------------------------- #
# download
# --------------------------------------------------------------------------- #
def load_manifest(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def download(session: requests.Session, pages: list[MonthPage], outdir: Path,
             delay: float, force: bool) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    manifest_path = outdir / "manifest.json"
    manifest = load_manifest(manifest_path)

    for i, page in enumerate(pages, 1):
        entry = manifest.get(page.url, {})
        headers = {}
        target = outdir / f"{page.stem}.html"
        if not force and target.exists():
            if etag := entry.get("etag"):
                headers["If-None-Match"] = etag
            if last := entry.get("last_modified"):
                headers["If-Modified-Since"] = last

        try:
            resp = session.get(page.url, headers=headers)
        except requests.RequestException as exc:
            log.error("[%d/%d] %s -> network error: %s", i, len(pages), page.url, exc)
            continue

        if resp.status_code == 304:
            log.info("[%d/%d] %s -> unchanged", i, len(pages), target.name)
        elif resp.status_code == 404:
            log.info("[%d/%d] %s -> not published", i, len(pages), page.url)
        elif resp.ok:
            body = resp.content                      # raw bytes, exactly as served
            digest = hashlib.sha256(body).hexdigest()
            if entry.get("sha256") == digest and target.exists() and not force:
                log.info("[%d/%d] %s -> identical body", i, len(pages), target.name)
            else:
                target.write_bytes(body)
                log.info("[%d/%d] %s -> saved %d bytes", i, len(pages), target.name, len(body))
            manifest[page.url] = {
                **asdict(page),
                "file": target.name,
                "sha256": digest,
                "etag": resp.headers.get("ETag"),
                "last_modified": resp.headers.get("Last-Modified"),
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "status": resp.status_code,
                "encoding": resp.encoding,
            }
        else:
            log.warning("[%d/%d] %s -> HTTP %d", i, len(pages), page.url, resp.status_code)

        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        if i < len(pages):
            time.sleep(delay)                        # be a polite client

    return manifest


# --------------------------------------------------------------------------- #
# parse
# --------------------------------------------------------------------------- #
#
# Only *_sujet.html pages can be parsed this way. *_correction.html pages sit
# behind the SWPM login (see swpm_login above) and use a completely different,
# gated layout - feeding one into parse_month() will just yield 0 questions.
#
# Layout note: WordPress's editor has each month's "sujet" post built by
# pasting several complete, standalone HTML documents ("combinaisons")
# straight into the post body - each with its own
# <!DOCTYPE html><html><head>...</head><body>...</body></html> wrapper. That's
# illegal nesting, but lxml recovers from it exactly like a browser would:
# it drops the redundant nested <html>/<head>/<body> tags and splices their
# children into the one real document, so by the time we walk the tree it's
# a single flat sequence we can scan top to bottom.
#
# We do NOT rely on any one fixed wrapper for the tache label or fixed
# <section> nesting for the sujet/question pair - both drift across the
# site's history:
#   * newer posts:  <div class="section-title">TÂCHE 2</div>
#   * 2024/2025 posts: <table><tr><td class="t2">TACHE 2</td></tr></table>
#   * one January 2026 combinaison has a stray "TÂCHE 2 🙂🙂" (emoji pasted
#     into the heading), which breaks any regex anchored to end-of-string.
#   * some combinaisons have their <section> tags mismatched/unclosed, so
#     <div class="custom-div">Sujet N</div> and the following
#     <nav class="custom-nav"> aren't reliably inside a shared <section>.
# So instead of keying off tag names/classes for the tache label, or
# <section> for grouping, we key off the visual grammar itself - same
# approach as scraper_reussir.py - and track state as we walk every tag:
# whichever tag's own text starts with "Tache N" sets the current tache,
# whichever <div class="custom-div"> starts with "Sujet N" sets the current
# sujet, and the next <nav class="custom-nav"> we meet is that sujet's
# question text. custom-div/custom-nav are the one pairing that has stayed
# consistent across every layout revision we've seen, so we still anchor on
# those rather than going fully text-driven for the question body.
#
# We deliberately do NOT decompose <nav> tags the way scraper_reussir.py
# strips site navigation - this site (mis)uses <nav class="custom-nav"> as
# the wrapper around the actual question text, not for navigation.

COMBINAISON_ID_RE = re.compile(r"^COMBINAISON_(\d+)$")
TACHE_RE = re.compile(r"^T[ÂA]CHE\s*(\d+)", re.I)     # no trailing $: tolerates "TÂCHE 2 🙂🙂"
SUJET_RE = re.compile(r"^Sujet\s*(\d+)", re.I)


@dataclass(frozen=True)
class Question:
    id: str            # source|year|month|tache|combinaison|sujet, e.g. "0220260820501"
    source: str
    month_slug: str    # "2026-03" - derived from the saved filename
    year: int
    month: int
    tache: int
    combinaison: int
    sujet: int
    text: str
    source_url: str
    scraped_at: str


def parse_month(html: str, stem: str, source_url: str) -> list[Question]:
    soup = BeautifulSoup(html, "lxml")
    for junk in soup(["script", "style"]):
        junk.decompose()

    date_part = stem.split("_", 1)[0]      # "2026-03_sujet" -> "2026-03"
    year_str, month_str = date_part.split("-")
    year, month = int(year_str), int(month_str)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    tache: int | None = None
    sujet: int | None = None
    combinaison = 0
    out: list[Question] = []
    for node in soup.descendants:
        if not isinstance(node, Tag):
            continue

        if node.name == "span" and (m := COMBINAISON_ID_RE.match(node.get("id") or "")):
            combinaison = int(m.group(1))
            continue

        # tache label: any tag, any wrapper - see the layout note above
        own_text = clean(node.get_text(" "))
        if m := TACHE_RE.match(own_text):
            tache = int(m.group(1))
            continue

        if node.name == "div" and "custom-div" in (node.get("class") or []):
            if m := SUJET_RE.match(own_text):
                sujet = int(m.group(1))
            continue

        if node.name == "nav" and "custom-nav" in (node.get("class") or []):
            if tache is None or sujet is None:
                continue
            text = clean(node.get_text(" "))
            if text:
                # Sujet numbering already resets per tache within a
                # combinaison (the site's own "Sujet 1..5" labels start over
                # under each TÂCHE heading - verified against every cached
                # month), so `sujet` as-is is exactly the id's per-tache
                # sequence number, no separate counter needed.
                out.append(Question(
                    id=f"{SOURCE_CODE:02d}{year:04d}{month:02d}{tache:d}{combinaison:02d}{sujet:02d}",
                    source=SOURCE,
                    month_slug=date_part,
                    year=year,
                    month=month,
                    tache=tache,
                    combinaison=combinaison,
                    sujet=sujet,
                    text=text,
                    source_url=source_url,
                    scraped_at=now,
                ))
            sujet = None      # consume it; one question per Sujet marker
            continue

    return out


def parse_all(raw_dir: Path, out_dir: Path) -> None:
    manifest = load_manifest(raw_dir / "manifest.json")
    url_of = {entry["file"]: url for url, entry in manifest.items()}

    files = sorted(raw_dir.glob("*_sujet.html"))
    if not files:
        sys.exit(f"no saved *_sujet.html pages in {raw_dir} - run `crawl` first "
                 f"(*_correction.html pages need a login and use a different layout)")

    out_dir.mkdir(parents=True, exist_ok=True)
    tache2_path = out_dir / "tache2.jsonl"
    tache3_path = out_dir / "tache3.jsonl"

    counts = {2: 0, 3: 0}
    with tache2_path.open("w", encoding="utf-8") as f2, \
         tache3_path.open("w", encoding="utf-8") as f3:
        for path in files:
            source_url = url_of.get(path.name, f"{BASE}/{path.stem}/")
            questions = parse_month(path.read_text(encoding="utf-8"), path.stem, source_url)
            if not questions:
                print(f"  ! {path.stem}: 0 questions - layout may differ", file=sys.stderr)
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
            print(f"{path.stem:24} tache2={n2:3}  tache3={n3:3}")

    print(f"\ntache 2: {counts[2]} -> {tache2_path}")
    print(f"tache 3: {counts[3]} -> {tache3_path}")


# --------------------------------------------------------------------------- #
# selftest (no network - validates extraction on a synthetic nested-HTML page)
# --------------------------------------------------------------------------- #

FIXTURE = """
<html><body>
<div class="wp-block-uagb-separator"><h5><span class="ez-toc-section" id="COMBINAISON_1"></span>COMBINAISON_1</h5></div>
<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8"><title>EXPRESSION ORALE COMBINAISON DE MARS 2026</title></head>
<body>
<div class="section-title">TÂCHE 2</div>
<section><div class="custom-div">Sujet 1</div>
  <nav class="custom-nav"><p>Je recherche un(e) colocataire. Vous me posez des questions
     sur le logement (prix, disponibilite, organisation, etc.).</p></nav></section>
<div class="section-title">TÂCHE 3</div>
<section><div class="custom-div">Sujet 1</div>
  <nav class="custom-nav"><p>Pensez-vous que les achats d'occasion sont une bonne idee ?</p></nav></section>
</body></html>
<div class="wp-block-uagb-separator"><h5><span class="ez-toc-section" id="COMBINAISON_2"></span>COMBINAISON_2</h5></div>
<!DOCTYPE html>
<html lang="fr"><head><title>EXPRESSION ORALE COMBINAISON DE MARS 2026</title></head>
<body>
<div class="section-title">TÂCHE 2</div>
<section><div class="custom-div">Sujet 1</div>
  <nav class="custom-nav"><p>Je suis votre collegue. Je vous pose des questions sur votre
     nouvelle formation a distance (organisation, tarifs, etc.).</p></nav></section>
</body></html>
<div class="wp-block-uagb-separator"><h5><span class="ez-toc-section" id="COMBINAISON_3"></span>COMBINAISON_3</h5></div>
<!DOCTYPE html>
<html lang="fr"><head><title>EXPRESSION ORALE COMBINAISON DE SEPTEMBRE 2025</title></head>
<body>
<table><tr><td class="t2">TACHE 2</td></tr></table>
<div class="custom-div">Sujet 1</div>
<nav class="custom-nav"><p>Je travaille a l'accueil. Vous demandez des renseignements
   sur un sejour de vacances (lieux, activites, tarifs, etc.).</p></nav>
<div class="section-title">TÂCHE 3 &#128578;&#128578;</div>
<section><div class="custom-div">Sujet 1</div>
  <nav class="custom-nav"><p>Le tourisme est-il toujours benefique pour un pays ?</p></nav></section>
</body></html>
</body></html>
"""


def selftest() -> None:
    qs = parse_month(FIXTURE, "2026-03_sujet", "https://example.test/expression-orale-mars-2026/")
    assert [(q.combinaison, q.tache, q.sujet) for q in qs] == [
        (1, 2, 1), (1, 3, 1), (2, 2, 1), (3, 2, 1), (3, 3, 1)
    ], "combinaison/tache/sujet tracking broke on the nested-HTML layout"
    # combinaison 3 exercises two real-world bugs found via crawled data:
    # the old <table class="t2"> layout (no "section-title" div at all), and
    # a label with trailing emoji ("TÂCHE 3 🙂🙂") that breaks any end-anchored regex.
    assert [q.tache for q in qs if q.combinaison == 3] == [2, 3], \
        "table-based tache label or emoji-suffixed label regressed"
    assert all(q.source == SOURCE for q in qs), "source field not stamped"
    assert qs[0].year == 2026 and qs[0].month == 3
    assert qs[0].id == "0220260320101", qs[0].id
    # qs[0] and qs[1] are both "Sujet 1" of combinaison 1 and differ only by
    # tache - the tache digit is what keeps their ids distinct.
    assert qs[0].tache != qs[1].tache and qs[0].id != qs[1].id
    assert len({q.id for q in qs}) == len(qs), "ids must be unique across taches"
    print("selftest passed:", len(qs), "questions across", qs[-1].combinaison, "combinaisons")


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #
def run_crawl(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    session = build_session(args.cookie_file, args.timeout)

    user, password = os.getenv("OPAL_USER"), os.getenv("OPAL_PASS")
    if not args.cookie_file and user and password:
        swpm_login(session, user, password)
    elif not args.cookie_file:
        log.warning("No cookie file and no OPAL_USER/OPAL_PASS - member-only content will be empty")

    if args.discover == "hub":
        pages = discover_from_hub(session, args.section)
        if not args.corrections:
            pages = [p for p in pages if p.kind == "sujet"]
        if not pages:
            log.warning("Hub discovery returned nothing - falling back to slug enumeration")
            pages = discover_from_slugs(args.years, args.corrections)
    else:
        pages = discover_from_slugs(args.years, args.corrections)

    log.info("%d page(s) queued", len(pages))
    if args.dry_run:
        for p in pages:
            print(f"{p.year}-{p.month:02d} {p.kind:<10} {p.url}")
        return 0

    manifest = download(session, pages, args.raw_dir, args.delay, args.force)
    saved = sum(1 for v in manifest.values() if v.get("status") == 200)
    log.info("Done. %d entries in manifest, output in %s", saved, args.raw_dir.resolve())
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("crawl", help="download raw HTML (needs SWPM login for *_correction pages)")
    c.add_argument("-o", "--raw-dir", default=Path("raw_opal"), type=Path)
    c.add_argument("--discover", choices=("hub", "slugs"), default="hub",
                   help="parse the hub page, or generate URLs from the slug pattern")
    c.add_argument("--section", default="sujet d'actualite",
                   help="heading text to look for (accent-insensitive)")
    c.add_argument("--years", nargs="+", type=int,
                   default=[time.gmtime().tm_year - 1, time.gmtime().tm_year],
                   help="years to enumerate when --discover slugs")
    c.add_argument("--corrections", action="store_true",
                   help="also fetch the *-correction pages")
    c.add_argument("--cookie-file", help="Netscape cookies.txt from a logged-in browser")
    c.add_argument("--delay", type=float, default=2.0, help="seconds between requests")
    c.add_argument("--timeout", type=int, default=30)
    c.add_argument("--force", action="store_true", help="ignore cache and re-save everything")
    c.add_argument("--dry-run", action="store_true", help="list URLs and exit")
    c.add_argument("-v", "--verbose", action="store_true")

    p = sub.add_parser("parse", help="extract Tache2/Tache3 questions from saved *_sujet.html pages")
    p.add_argument("--raw-dir", type=Path, default=Path("raw_opal"))
    p.add_argument("--out-dir", type=Path, default=Path("questions_opal"),
                   help="directory to write tache2.jsonl and tache3.jsonl into")

    sub.add_parser("selftest", help="run the offline parser test")

    args = ap.parse_args(argv)
    if args.cmd == "crawl":
        return run_crawl(args)
    elif args.cmd == "parse":
        parse_all(args.raw_dir, args.out_dir)
        return 0
    else:
        selftest()
        return 0


if __name__ == "__main__":
    sys.exit(main())