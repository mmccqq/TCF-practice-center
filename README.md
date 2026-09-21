# TCF Practice Center

Preparation platform for the **TCF Canada** speaking exam, built around *real past
questions* rather than generic French practice. Questions are scraped from three
public sources, deduplicated across them, labelled with an LLM-assisted pipeline,
and served as a frequency-ranked core set.

**Live:** [happytcf.onrender.com](https://happytcf.onrender.com)(take 30s to wake the service on Render)

The product claim is compression. 4,386 scraped sightings of Task 2 questions
collapse into 1,541 distinct questions, which group into **89 core subjects** —
so a candidate works through the subjects the exam keeps returning to instead of
reading a thousand prompts in date order.

## Stack

| Layer | Choice |
|---|---|
| Backend | FastAPI, SQLAlchemy 2.0, Alembic |
| Database | PostgreSQL (Neon) in production, SQLite locally |
| Auth | bcrypt, HS256 JWT, Google OAuth2 authorization-code flow |
| Frontend | React 19 + Vite, React Router, TanStack Query, Tailwind v4 |
| Deploy | One Docker image on Render: FastAPI serves `/api/*` and the React bundle |
| Labelling | OpenAI / DeepSeek / Gemini / Anthropic, run from the admin system |

Single origin is deliberate: the Google sign-in link is relative and the `g_state`
CSRF cookie must be set and read by the same host, so splitting the frontend onto
a static host would have meant code changes plus CORS.

## The data model

Four layers, because one table was being asked to be two things at once — an
identity ("this is one question") and a timeline entry ("this was asked in
September"). See [Core question set design.md](Core%20question%20set%20design.md).

```
raw_questions     one scraped sighting          provenance, internal only
  └─ fingerprints one unique text               identity; labels and progress hang here
       └─ list_questions   one row per month    what the list page renders
            themes / core_subjects              controlled vocabulary, enforced by FK
```

Why it matters: a question that recurs across eighteen months is eighteen rows on
the timeline sharing **one** fingerprint, so it carries one set of labels and one
"practised" state. The previous single-table model moved a recurring question's id
to the newest month and left the old row behind — 49 questions had an unlabelled
twin before this was fixed.

Frequency is counted in **months**, not in scraped reports: 18% of month-entries
were filed by more than one source, so counting reports would overstate how often
the exam actually asked.

## Quick start

```bash
# backend
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
backend/.venv/bin/alembic -c backend/alembic.ini upgrade head
(cd backend && .venv/bin/uvicorn app.main:app --reload)      # :8000

# frontend, separate terminal
cd frontend && npm install && npm run dev                    # :5173
```

Open http://localhost:5173 — Vite proxies `/api` to FastAPI, so no CORS setup or
hardcoded backend URL in development. API docs at http://localhost:8000/docs.

A fresh database is empty. Fill it from the admin system (below), or locally:

```bash
cd questions_processing
python3 scraper_reussir.py crawl && python3 scraper_reussir.py parse
cd ../backend && .venv/bin/python backfill_questions.py --dry-run
```

## Configuration

`backend/.env` is loaded into the real process environment, so it covers both
the app's own settings and the provider keys `llm.py` reads:

| variable | notes |
|---|---|
| `DATABASE_URL` | must use the `postgresql+psycopg://` scheme; use Neon's **direct** host, not `-pooler` |
| `SECRET_KEY` | JWT signing; the app refuses to start on a dev default when `ENV=production` |
| `FRONTEND_ORIGIN` / `BACKEND_ORIGIN` | **no trailing slash** — the OAuth callback is built by appending a path |
| `GOOGLE_CLIENT_ID` / `_SECRET` | optional; the button reports "not configured" without them |
| `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, … | optional; a key can also be pasted per run in the admin UI |

On Render the same names go in the Environment tab. A shell `export` beats
`.env`, which is why `DATABASE_URL='postgresql+psycopg://…' <command>` targets
production from a laptop.

## Admin system

Gated on `users.is_admin`, and it covers the whole pipeline in the browser.
Grant yourself access — there is no self-promotion endpoint:

```bash
backend/.venv/bin/python backend/make_admin.py you@example.com
```

| tab | what it does |
|---|---|
| **Data** | one action: check each source for months the bank lacks, download only those pages, parse them in memory, load all three layers. No HTML is stored — the database records which months are in |
| **Runs** | live progress, timings, cancel, and a link to results. A job killed by a deploy or an idle host is reported `interrupted`, not left looking alive |
| **Vocabulary** | themes and core subjects with usage counts; add, rename, move, merge. Deleting a label in use is refused — merge repoints its questions first |
| **Labelling** | filter to what is missing a label, edit inline or in bulk, export to `.xlsx`, or select questions and start a labelling run |
| **Review** | adjudicate answers; each candidate shows how many questions already use it. Applying writes to the bank and reports labels the vocabulary lacks, with one click to add and retry |
| **Compare** | two models' answers land in **one** batch, so accepting the agreed ones and settling the rest applies in a single pass |

A labelling round is four steps rather than fourteen: fetch → run → accept the
agreements → review the rest.

### Why agreement, not accuracy

Two models agreeing is used as *triage*, not as a correctness claim. Against a
hand-built gold set, a single model scored 85–86% and agreement-filtered answers
90–92%. Agreement tells you where a human is worth spending; it does not tell you
the answer is right.

## Layout

```
backend/
  app/
    config.py        settings; loads .env into os.environ
    models.py        the four layers, vocabulary, users, progress, jobs, review
    routers/         questions, auth, progress, admin, admin_review, admin_jobs
    llm_runner.py    labelling jobs on a background thread
    scrape_runner.py scrape → parse → load, in memory
  backfill_questions.py   scraper JSONL → the three question layers
  load_vocabulary.py      llm_tasks.VOCABULARY → themes / core_subjects
  load_labels.py          labelling output → fingerprints
  export_questions.py     questions → JSONL for local tooling
  make_admin.py           grant or revoke admin
  migrations/             Alembic; every schema change since the first deploy
frontend/src/
  pages/           Home, QuestionList, Frequent (core set), Bookmarks, AuthPage
  pages/admin/     Data, Jobs, Vocabulary, Labelling, Review, Compare
  lib/             api client, auth context, progress hooks, toasts
questions_processing/
  scraper_*.py     crawl (network) and parse (offline), kept separate
  deduplication.py normalise + SHA-256 fingerprint; TF-IDF and embedding tiers
  llm.py           provider layer, chunking, resume, batch API
  llm_tasks.py     prompts, schemas and the controlled vocabulary
```

`questions_processing/` is copied into the Docker image because the admin system
imports it at runtime; its data directories are excluded by `.dockerignore`.

## Question ids

Scraper ids are stable and meaningful:
`source(2) · year(4) · month(2) · tache(1) · partie(2) · sujet(2)` — so
`0320260820401` is reussir / 2026-08 / Task 2 / partie 4 / sujet 1. Sources are
`01` formation, `02` opal, `03` reussir.

Stability matters: labels and progress are keyed on the fingerprint, but a
scraper id is what lets an older label file still find its question after the
wording was corrected.

## Deduplication

Tier 1 only, in the pipeline: normalise (lowercase, strip accents and
punctuation), then SHA-256. Grouping is on `(tache, fingerprint)` because the
same wording under Task 2 and Task 3 is two different questions.

Semantic merging is **deferred, not done**. `deduplication.py` has TF-IDF and
embedding tiers, and benchmarking them on this corpus is why TF-IDF was chosen
for that work: e5-large compressed every pair into 0.81–1.00, while TF-IDF put
the median at 0.03 — a usable threshold versus none. Wiring it into the pipeline
is a `canonical_id` on `fingerprints`; nothing else moves, which is why the
fingerprint id is a surrogate rather than the hash.

## Migrations

```bash
cd backend
.venv/bin/alembic upgrade head                                  # local
DATABASE_URL='postgresql+psycopg://…' .venv/bin/alembic upgrade head   # Neon
```

Render's free tier has no pre-deploy hook, so migrations are run from a laptop
against Neon. Additive migrations can go either side of a push; from the moment
code reads a new table, migrate **first**.

The chain includes a six-step expand/contract move from the original `questions`
table to the four-layer model, with hand-written data-carrying steps — Alembic's
autogeneration would have dropped columns before their contents were copied.

## Tests

```bash
backend/.venv/bin/python -m pytest backend/tests -q
python3 questions_processing/llm.py selftest        # prompts vs schemas, offline
python3 questions_processing/scraper_reussir.py selftest
```

## Roadmap

- ✅ question bank, accounts, Google sign-in
- ✅ four-layer data model, month-grouped timeline, core set
- ✅ per-question progress and bookmarks, keyed to the question not the month
- ✅ admin: scraping, labelling runs, vocabulary, review, comparison
- ⬜ Task 3 vocabulary and labelling (its themes do not exist yet)
- ⬜ answer templates, strategies and model responses
- ⬜ personal material library with fork-on-edit
- ⬜ spaced-repetition flashcards
