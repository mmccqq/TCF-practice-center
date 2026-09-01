# Backend learning notes — 2026-08-31

A record of the questions I asked while reading through the phase 0 backend,
with the short version of each answer. Files referenced:
`backend/app/db.py`, `models.py`, `schemas.py`, `routers/questions.py`, `backend/seed.py`.

---

## The three-layer picture

Everything below fits into this shape. If I remember one diagram, it is this one.

```text
┌──────────────────────────────────────────────────────┐
│  schemas.py   (Pydantic)                             │
│  What the API accepts and returns.                   │
│  Validates, converts, serialises, documents.         │
└─────────────────────┬────────────────────────────────┘
                      │  from_attributes=True
┌─────────────────────┴────────────────────────────────┐
│  models.py    (SQLAlchemy)                           │
│  What the database stores. Tables, columns, indexes. │
└─────────────────────┬────────────────────────────────┘
                      │
┌─────────────────────┴────────────────────────────────┐
│  db.py                                               │
│  How to reach the database. Engine, sessions, get_db.│
└──────────────────────────────────────────────────────┘
```

---

# Part 1 — `db.py`

## Q: What are `.db` and `Base`?

`.db` is a **relative import** — the sibling module `backend/app/db.py`. The leading
dot means "same package as me."

`Base` is the SQLAlchemy declarative base. Subclassing it is what turns a plain
Python class into a mapped table.

```python
class Base(DeclarativeBase):
    pass
```

## Q: Why an empty class? Why not inherit `DeclarativeBase` directly?

Because SQLAlchemy forbids it:

```text
InvalidRequestError: Cannot use 'DeclarativeBase' directly as a
declarative base class. Create a Base by creating a subclass of it.
```

The reason: **one base = one registry.** Two separate bases have separate
`metadata`, so `create_all()` on one would not see the other's tables, and
`relationship("Attempt")` could not resolve by name.

```text
                Base
                 │
    ┌────────────┼────────────┐
    ▼            ▼            ▼
  User       Question      Attempt
    └────────────┴────────────┘
                 │
          Base.metadata  →  create_all()  →  CREATE TABLE ×3
```

The empty class is also the place to put project-wide settings later
(naming conventions, shared columns).

## Q: What is the difference between flush and commit?

```text
session.add(obj)  →  flush()  →  SQL sent  →  commit()  →  permanent
                                    │
                               rollback()
                                    │
                                discarded
```

Verified in the project venv:

```text
after add      -> u.id = None
after flush    -> u.id = 1
  seen by this session : 1
  seen by other session: 0     ← flush is not visible outside
after rollback -> rows: 0      ← flushed data can still be undone
after commit   -> rows: 1
```

|  | `flush()` | `commit()` |
|---|---|---|
| Sends SQL | yes | yes |
| Assigns generated IDs | yes | yes |
| **Ends the transaction** | **no** | **yes** |
| Visible to other connections | no | yes |
| Undoable by `rollback()` | **yes** | no |

`commit` = `flush` + end the transaction.

`autoflush=False` in `SessionLocal` means SQLAlchemy will not flush
automatically before each query — I control when SQL is emitted.

## Q: Why does `get_db` use `yield` instead of `return`?

`return` ends the function, so `db.close()` could never run and connections
would leak. `yield` **pauses** it, creating a before and an after.

```python
def get_db():
    db = SessionLocal()      # PHASE 1: before the endpoint
    try:
        yield db             # PAUSE — FastAPI runs the endpoint here
    finally:
        db.close()           # PHASE 2: after the response is sent
```

`finally` runs even if the endpoint raises. `close()` also rolls back anything
uncommitted, so a crash mid-write leaves nothing half-applied.

## Other `db.py` details

- `check_same_thread=False` — SQLite refuses cross-thread connection use by
  default; FastAPI runs sync endpoints in a threadpool, so the guard must go.
  The `else {}` is what keeps it correct for Postgres (psycopg would raise on
  an unknown kwarg — the code comment saying it is "ignored" is slightly off).
- `future=True` — a SQLAlchemy 1.4 leftover. No effect on 2.0.
- `autocommit=False` — redundant on 2.0; `True` is no longer supported at all.
- `create_engine()` does **not** connect. It connects on first use.

---

# Part 2 — `models.py`

## Q: Does this follow normal Python syntax?

Yes, entirely. Every field line is a standard **annotated assignment**
(PEP 526):

```python
    email  :  Mapped[str]  =  mapped_column(String(320), unique=True, index=True)
#   ^name     ^annotation     ^value
```

- The annotation is **never evaluated** (`from __future__ import annotations`
  stores it as a string in `__annotations__`).
- Only the right-hand side runs.
- `Mapped[str]` is ordinary generic subscripting; `"Attempt"` in quotes is a
  forward reference to a class defined later in the file.
- SQLAlchemy's contribution is at runtime: a metaclass inherited from `Base`
  reads both the annotations and the assigned values.

Two things that are not what they look like:

```text
user.email     (instance) → "a@b.com"      a plain string
User.email     (class)    → SQL expression  → that is why == builds SQL
```

## Q: What is `utcnow()` for?

```python
def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)
```

Returns a **timezone-aware** UTC datetime. The trap it avoids:

```text
dt.datetime.now()                 # naive, local time
dt.datetime.utcnow()              # naive, but UTC values ← lies about itself
dt.datetime.now(dt.timezone.utc)  # aware ✓
```

Passed **uncalled** to SQLAlchemy so it re-runs per row:

```text
default=utcnow      → SQLAlchemy calls it at every insert  ✓
default=utcnow()    → frozen at import time, every row identical  ✗
```

Caveat verified: SQLite drops the offset. Stored as
`2026-08-31 23:14:01.922138`, read back with `tzinfo=None`. Postgres will keep
it. Until then, do not compare a value read from the DB against `utcnow()`.

## Q: What does "denormalised" mean for `period`?

Storing data already derivable from other columns. `period` is just
`f"{year:04d}-{month:02d}"`.

Bought: sorting/filtering on one column, and one usable index.
Cost: `year`, `month`, `period` can drift out of sync — nothing enforces it.

Payoff seen later: because the format is zero-padded and largest-unit-first,
sorting it as **text** gives correct **chronological** order. No date parsing.

## Q: What does `__table_args__` do?

The slot for table-level constructs that belong to no single column. Must be a
tuple — note the trailing comma.

```python
    __table_args__ = (
        Index("ix_questions_tache_period", "tache", "period"),
    )
```

A **composite** index is sorted by `tache` first, then `period` within each.
Leftmost-prefix rule:

| Query | Uses `(tache, period)`? |
|---|---|
| `WHERE tache = 3` | yes |
| `WHERE tache = 3 AND period = '2024-05'` | yes |
| `WHERE tache = 3 ORDER BY period` | yes |
| `WHERE period = '2024-05'` | **no** |

## Q: What does `UniqueConstraint` do?

Enforces that the **pair** is unique, not each column.

```text
7  "2023051301021"   ok
7  "2023051301022"   ok — same user, different question
9  "2023051301021"   ok — same question, different user
7  "2023051301021"   REJECTED
```

Encodes the rule: one progress row per user per question. Violation raises
`IntegrityError`. Most databases back it with an index, so it also gives a free
composite index on `(user_id, question_id)`.

## Q: What is the `name=` parameter for?

```sql
CONSTRAINT uq_attempt_user_question UNIQUE (user_id, question_id)
```

Mainly **migrations** — Alembic needs a stable name to emit
`ALTER TABLE ... DROP CONSTRAINT <name>`, and auto-generated names differ per
backend.

Prefixes: `ix_` index, `uq_` unique, `fk_` foreign key, `pk_` primary key,
`ck_` check.

Error messages quote the name on Postgres. SQLite does not — it reports
`UNIQUE constraint failed: attempts.user_id, attempts.question_id`.

Alternative: set a `naming_convention` on the `MetaData` once and stop naming
things by hand.

---

# Part 3 — `schemas.py`

## Q: What does Pydantic do?

Four jobs:

```text
1. Validation     → reject bad data, clear per-field errors
2. Conversion     → "3" becomes 3, automatically
3. Serialisation  → Python objects become JSON
4. Documentation  → /docs is generated from the schemas
```

Real output from `SignUpIn(email='not-an-email', password='short')`:

```text
email    | value_error      | value is not a valid email address
password | string_too_short | String should have at least 8 characters
```

Note it reports **every** error at once, not just the first.

Key idea: **you describe, Pydantic enforces.** The type annotations *are* the
validation rules — no `isinstance` checks anywhere.

## Q: What is `model_config = ConfigDict(from_attributes=True)`?

```text
from_attributes = False  →  Pydantic looks for  obj["id"]   (dict access)
from_attributes = True   →  Pydantic looks for  obj.id      (attribute access)
```

Without it, converting a SQLAlchemy row raises:

```text
ValidationError: Input should be a valid dictionary or instance of ...
```

It **adds** an ability; dicts still work. Pydantic v1 called it
`class Config: orm_mode = True`.

Where it belongs:

| Schema | Has it? | Why |
|---|---|---|
| `QuestionOut`, `UserOut` | yes | built from a DB row |
| `SignUpIn`, `LoginIn`, `ChangePasswordIn` | no | built from request JSON (already a dict) |
| `QuestionPage`, `TokenOut` | no | built from a dict in code |

The separation is a **security boundary**: `User.hashed_password` exists on the
model but not on `UserOut`, so it cannot leak into a response.

*(I added this summary as a module docstring in `schemas.py`.)*

---

# Part 4 — `routers/questions.py`

## Q: What does "endpoint" mean?

> endpoint = **method** + **URL path** + the **function** that handles it.

The app's real routes:

```text
GET   /api/questions
GET   /api/questions/meta
GET   /api/questions/{question_id}
POST  /api/auth/signup
POST  /api/auth/login
GET   /api/auth/me
POST  /api/auth/change-password
GET   /api/auth/google/config
GET   /api/auth/google/login
GET   /api/auth/google/callback
GET   /api/health
```

- The **method is part of the identity** — `GET /x` and `POST /x` are two
  different endpoints.
- The **decorator creates it**: `@router.get("/meta")`. Without one, a function
  is unreachable.
- Path is `prefix + decorator path`.
- Two parameter kinds: path `/{question_id}` says *which*; query
  `?tache=2&page=3` says *how to filter*.
- **Route order matters.** `/meta` is declared before `/{question_id}`, or a
  request for `/meta` would match the pattern and look for a question with
  id `"meta"`.

## Q: What are `ge` and `le`?

```text
gt  >     strictly greater
ge  >=    greater or equal
lt  <     strictly less
le  <=    less or equal
```

Words, not symbols, because keyword arguments must be valid Python names.
The names come from Python's own `__ge__` / `__le__` methods.

`tache: int = Query(..., ge=2, le=3)` — the `...` means **required**.

Real responses:

```text
?tache=1          → 422  Input should be greater than or equal to 2
?tache=4          → 422  Input should be less than or equal to 3
?per_page=500     → 422  Input should be less than or equal to 100
?tache=abc        → 422  Input should be a valid integer
```

Order: type conversion first, then constraints. The endpoint body never runs on
failure, so it can trust its arguments completely.

`per_page: le=100` is a **protection**, not a convenience — it stops a stranger
requesting `?per_page=999999` and exhausting the server.

## Q: What does `where = [Question.tache == tache]` do?

The surprising part: on a **class**, `==` does not compare. It builds SQL.

```text
plain python    3 == 3               -> True
on the CLASS    Question.tache == 3  -> <BinaryExpression>
on an INSTANCE  q.tache == 3         -> True
```

The expression prints as `questions.tache = :tache_1` — a SQL fragment with a
**placeholder**, not the value.

A list because it grows:

```python
    where = [Question.tache == tache]   # always applies (tache is required)
    if q:      where.append(Question.text.ilike(f"%{q}%"))
    if source: where.append(Question.source == source)
    ...
```

`.where(*where)` unpacks it; multiple conditions are joined with `AND`. One
list handles all 16 combinations of four optional filters.

## Q: What SQL is behind the count line?

```python
db.scalar(select(func.count()).select_from(Question).where(*where))
```

```sql
SELECT count(*) AS count_1
FROM questions
WHERE questions.tache = :tache_1
  AND lower(questions.text) LIKE lower(:text_1)
  AND questions.year = :year_1
```

with `{'tache_1': 2, 'text_1': '%bonjour%', 'year_1': 2023}` sent separately.

- `func.count()` → `count(*)`. `func.<anything>` passes the name straight
  through to SQL.
- `.select_from(Question)` is needed because `count()` mentions no table, so
  there is nothing to infer `FROM` from.
- `ilike` → `lower(x) LIKE lower(y)` on SQLite (no native `ILIKE`); Postgres
  would use the real operator. Chosen automatically.
- Values never enter the SQL text → SQL-injection defence **and** query-plan
  caching.
- `db.scalar()` takes `row[0][0]`: `[(1599,)]` → `1599`.
- `or 0` is defensive; `COUNT` never returns NULL. It would matter for `SUM`.

Two queries are needed — the count without a limit, the rows with
`LIMIT/OFFSET` — so the UI can say "showing 1–25 of 1,599."

## Q: The `/meta` endpoint, line by line

Real response:

```json
{
  "counts":  { "tache2": 1599, "tache3": 1003 },
  "sources": ["formation", "opal", "reussir"],
  "periods": ["2026-08", "2026-07", ... 46 total],
  "sorts":   ["date_asc", "date_desc", "frequency"]
}
```

**`GROUP BY` turns one total into a breakdown:**

```sql
SELECT questions.tache, count(*) FROM questions GROUP BY questions.tache
```
```text
[(2, 1599), (3, 1003)]  →  dict()  →  {2: 1599, 3: 1003}
```

`dict()` accepts a list of 2-tuples directly.

**Single-column queries return 1-tuples:**

```text
[('formation',), ('opal',), ('reussir',)]     ← note the trailing commas
```

Unpacked with `[s for (s,) in rows]`. The `(s,)` pattern needs that comma.
Simpler alternative: `db.scalars(...)` does it automatically.

**`.get(2, 0)` not `[2]`** — `GROUP BY` only produces rows for values that
exist, so a missing tâche would be a `KeyError` → 500 error.

**`sorted(SORTS)`** — the same dict validates the `sort` parameter at line 38.
One source of truth, so advertised options and accepted options cannot drift.

## Q: How does `db.get()` retrieve data? Why not `execute` or `scalar`?

It **is** a query, specialised for primary-key lookup, with an extra shortcut.

Measured query counts:

```text
1st db.get()    -> 1 query   ['SELECT ... questions.id = ?']
2nd db.get()    -> 0 queries  ← served from the identity map
same object?      True
select+scalars  -> 1 query   (always queries)
same object?      True        ← identity map still deduplicates the instance
missing key     -> None, 1 query
```

The **identity map** is a per-session dict of everything loaded, keyed by
(class, primary key).

```text
db.get()   → checks the map BEFORE querying  → can skip SQL entirely
select()   → checks the map AFTER  querying  → always queries, still dedupes
```

Returns `None` when missing — does not raise. "Not found" is a normal outcome.

Choosing the right method:

```text
by primary key, one row  →  db.get()
one computed value       →  db.scalar()
many objects of a type   →  db.scalars()
multi-column tuples      →  db.execute()
```

`get` **only** works on the primary key. Anything with filtering, sorting, or
aggregating needs a real `select`.

Note: `get_db` makes a fresh session per request, so the identity map never
survives past one HTTP request.

---

# Part 5 — `seed.py`

## Q: Which file creates `tcf.db` and loads the JSON data?

Split between two files:

```text
backend/app/main.py   line 20   creates the TABLES on every server startup
backend/seed.py       line 114  creates the tables AND fills them
```

The `.db` **file** is created by SQLite automatically on first connection.
`Base.metadata.create_all()` is safe to re-run — it only creates what is missing.

`seed.py` is a standalone script, run by hand:

```bash
backend/.venv/bin/python backend/seed.py            # load everything
backend/.venv/bin/python backend/seed.py --reset    # wipe questions first
```

## `.json` vs `.jsonl`

The data files are **JSONL** — one complete JSON object per line, no wrapping
array, no commas. Readable line by line (`json.loads(line)`), appendable by a
scraper. A `.json` file would need `json.load(fh)` and full-file parsing.

## The numbers

```text
6 files (tache2 + tache3 × 3 sources)     7,531 raw rows
                    │
              deduplication
                    │
                    ▼
            questions table                2,602 rows
```

Grouped on `(tache, fingerprint(text))`, sorted by period, **newest sighting
kept as canonical**, and the group size stored as `occurrences` — which is what
makes the `"frequency"` sort and the phase 2 high-frequency banks possible.

`wanted()` filters the glob down to exactly `tache2`/`tache3`, skipping derived
files like `tache3_semantic.jsonl`.

## The write is an upsert

```python
existing = {q.id for q in db.query(Question.id).all()}
for rec in records:
    if rec["id"] in existing:  ...update(rec)
    else:                      db.add(Question(**rec))
db.commit()          # ← outside the loop: all 2,602 writes in ONE transaction
```

Idempotent — re-scrape and re-run without duplicating.
`Question(**rec)` works because the dict keys were named to match the columns.

---

# Quick self-test for tomorrow

Cover the answers and try these.

1. Why can't a model inherit from `DeclarativeBase` directly?
2. After `db.flush()`, can another connection see the row? After `db.commit()`?
3. What would break if `get_db` used `return` instead of `yield`?
4. What does `Question.tache == 2` evaluate to? What about `q.tache == 2`?
5. Which schemas need `from_attributes=True`, and why not the others?
6. Why is `select_from(Question)` necessary with `func.count()`?
7. Why must `/meta` be declared before `/{question_id}`?
8. What does `per_page: le=100` protect against?
9. When does `db.get()` emit no SQL at all?
10. Why does the count query run separately from the rows query?
11. How do 7,531 JSONL rows become 2,602 table rows?
12. Why is `sorted(SORTS)` returned by `/meta` rather than a hardcoded list?

---

# The ideas worth carrying forward

**Something wrapping something else.** `Base` outlives any one model so it can
hold what they share. A transaction wraps several flushes so they succeed or
fail together. `get_db` wraps the endpoint so setup and cleanup surround it.

**Declare, don't check.** Type annotations become validation rules, columns,
and documentation. Almost no imperative checking code anywhere in this backend.

**One source of truth.** `SORTS` is both the advertised and the accepted sort
list. `Base.metadata` is the one table registry. The same `where` list feeds
both the count and the page query. Whenever two things must agree, the code
derives one from the other rather than repeating it.
