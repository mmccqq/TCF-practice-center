# TCF Practice Center

Preparation platform for the **TCF Canada** exam built around *real past questions*
rather than generic French practice. Questions are scraped from public practice
sources, deduplicated across them, and served with search, filtering and
frequency counts.

**Status: Phase 0 complete.** Question bank for Expression Orale Tasks 2 and 3,
with accounts. Other sections and later phases are stubbed as "coming soon".

## Stack

| Layer | Choice |
|---|---|
| Backend | FastAPI, SQLAlchemy 2.0, SQLite (Postgres-ready) |
| Auth | bcrypt password hashing, HS256 JWT sessions, Google OAuth2 |
| Frontend | React 19 + Vite, React Router, TanStack Query, Tailwind CSS v4 |
| Data pipeline | Python scrapers → JSONL → Excel round-trip → dedup → SQLite |

## Quick start

```bash
# 1. backend
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
cp backend/.env.example backend/.env          # optional: add Google credentials
backend/.venv/bin/python backend/seed.py      # load ~2.6k questions into SQLite
(cd backend && .venv/bin/uvicorn app.main:app --reload)   # :8000

# 2. frontend  (separate terminal)
cd frontend && npm install && npm run dev                  # :5173
```

Open http://localhost:5173. The Vite dev server proxies `/api` to FastAPI, so
no CORS setup or hardcoded backend URL is needed in development.

API docs: http://localhost:8000/docs

## Tests

```bash
backend/.venv/bin/python -m pytest backend/tests -q
```

## Google sign-in

Optional — the button reports "not configured" until you set it up.

1. Create an OAuth 2.0 Client ID (Web application) in the
   [Google Cloud console](https://console.cloud.google.com/apis/credentials).
2. Add authorised redirect URI `http://localhost:8000/api/auth/google/callback`.
3. Put the client ID and secret in `backend/.env`.

Flow is server-side authorization-code: the client secret never reaches the
browser, and the callback hands the SPA a session JWT in the URL fragment.

## Layout

```
backend/
  app/
    config.py      settings; anchors relative sqlite paths to backend/
    db.py          engine + session
    models.py      User, Question, Attempt (Attempt is for phase 2)
    auth.py        hashing, JWT, current-user dependency
    routers/       questions.py, auth.py
  seed.py          JSONL -> SQLite, idempotent, collapses duplicates
  tests/
frontend/
  src/
    lib/           api client, auth context
    components/    Layout
    pages/         Home, QuestionList, AuthPage
scraper_formation.py / scraper_opal.py / scraper_reussir.py
deduplication.py            three-tier dedup (exact + embedding)
jsonl_to_xlsx.py / xlsx_to_jsonl.py   lossless Excel round-trip for hand review
```

## Data pipeline

Each scraper has `crawl` (network) and `parse` (offline) stages, kept separate so
a selector fix costs a re-parse, not a re-crawl.

```bash
python3 scraper_reussir.py crawl --raw-dir raw_reussir
python3 scraper_reussir.py parse --raw-dir raw_reussir --out-dir questions_reussir
```

Question ids are stable and meaningful:
`source(2) · year(4) · month(2) · tache(1) · partie(2) · sujet(2)` —
e.g. `0320260820401` is reussir / 2026-08 / Task 2 / partie 4 / sujet 1.
Sources are `01` formation, `02` opal, `03` reussir.

`seed.py` collapses repeat sightings of the same question on normalised text,
keeping the newest id as canonical and storing the count in `occurrences` —
which is what the phase 2 high-frequency banks will read.

## Roadmap

- **Phase 0** ✅ index page, Task 2/3 banks, database, accounts + Google sign-in
- **Phase 2** high-frequency banks (6/12 months), per-question progress tracking
- **Phase 3** answer templates and strategies, model responses
- **Phase 4** personal material library with fork-on-edit
- **Phase 5** spaced-repetition flashcards
