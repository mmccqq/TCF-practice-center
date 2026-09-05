# Core question set — design

Status: agreed, not implemented. Drafted 2026-09-04.

The product goal: a bounded set of questions a user can work through and, on
finishing, feel prepared. This document defines the data model that makes such
a set stable enough to track progress against.

---

## 1. The core idea

**The thing a user practises is a *cluster* — one real exam question — not a
scraped row.** Scraped rows are evidence that the cluster exists.

```text
 raw HTML          raw_question          cluster              version
 ────────          ────────────          ───────              ───────
 raw_reussir/  ─►  one scraped row  ─►   one real question ─► frozen snapshot
                   provenance            what users touch      what users study
                   (internal)            (permanent id)        (immutable text)
```

Today's `questions` table is really a cluster table under the wrong name:
`seed.py` collapses duplicates at load time and keeps only a count, discarding
which rows were collapsed. That discarded membership is what this design
restores.

---

## 2. Entities

### `raw_question` — what a scraper found

| field | example | notes |
|---|---|---|
| `id` | `"0320231120105"` | the scrapers' 13-digit id: source, year, month, tache, partie, sujet |
| `text` | | exactly as scraped |
| `source` | `"reussir"` | **internal only**, never returned by the API |
| `period` | `"2023-11"` | |
| `partie` | `1` | which exam session within the month |
| `cluster_id` | → `cluster.id` | assigned by the pipeline |

Never reaches a user. Exists so occurrences can be recounted, clusters can be
rebuilt, and "where did this come from" can be answered.

### `cluster` — the question

| field | notes |
|---|---|
| `id` | opaque integer, **allocated once, permanent** |
| `tache` | 2 or 3 |
| `representative_text` | current best wording; may improve over time |
| `representative_raw_id` | which `raw_question` it was taken from |
| `sighting_count` | derived: number of `raw_question` rows |
| `appearances` | derived: distinct `(period, partie)` — see §5 |

Everything user-facing points here: attempts, bookmarks, notes, ratings, theme
labels, API URLs.

### `version` + `core_set_item` — a published snapshot

```text
version
  id            opaque / sequential          NOT "2026-Q1"; see below
  label         "Autumn 2026 set"            human-readable, set by hand
  created_at, published_at

core_set_item
  version_id, cluster_id, rank
  representative_text                        frozen copy at publication
```

The version id carries **no calendar meaning**. Quarterly is the intended
rhythm, not a constraint — an off-schedule release is simply the next id.

### `pair_decision` — settled grey-band judgements

```text
a_raw_id, b_raw_id, verdict (merge | reject), decided_by (llm | human), decided_at
```

Written by the LLM tier and by `review.html`'s export. Both produce the same
shape, so one loader handles both.

---

## 3. The three ids and their lifetimes

```text
raw_question.id   "0320231120105"   internal. provenance, recount, re-clustering.
cluster.id        42                the question. progress, bookmarks, themes, URLs.
version.id        7                 which snapshot this user is working through.
```

The test that separates them: **which one would break a user's history if it
changed?** Only `cluster.id`. That is the one treated as permanent.

---

## 4. Pipeline

```text
scraper ──► raw_question rows
                 │
                 ▼
          tier 0-1  normalise + fingerprint       exact duplicates
                 │
                 ▼
          tier 2    TF-IDF cosine                 >= auto_merge -> same cluster
                 │                                grey band ↓
                 ▼
          tier 3    pair_decision
                      ├─ already decided? use it, ask nothing
                      └─ else LLM or review.html -> store the verdict
                 │
                 ▼
          assign    join an existing cluster, or allocate a NEW id
                 │
                 ▼
          select    rank clusters -> core_set_item for the next version
```

### Invariants

These are the point of the design; the tables are bookkeeping.

**1. Cluster ids are allocated, never recomputed.**

```text
✗  re-cluster everything each quarter, number the result 1..N
       a user practised cluster 42; next quarter cluster 42 is something else

✓  new question -> join an existing cluster, or take the next unused id
```

A full re-clustering is a deliberate, one-off migration — never part of the
routine job.

**2. Grey-band verdicts are stored, not re-derived.** Without `pair_decision`,
the same borderline pair can merge in one run and split in the next, silently
changing what someone is mid-way through studying — and the same LLM tokens get
paid for repeatedly.

**3. All user data keys on `cluster_id`.** No user-facing table ever holds a
scraper id. Otherwise a user who practised one wording is asked to repeat the
question the moment two wordings merge.

**4. `core_set_item` freezes the text.** The cluster's representative may keep
improving; the version a user is on must not move under them.

---

## 5. Counting: `sighting_count` vs `appearances`

Measured on the combined Tache 2 set (1406 clusters, 4231 sightings), clusters
whose sightings repeat within one month break down as:

```text
  500  cross-source                    3 sources reporting ONE exam appearance
  185  same source, different partie   two GENUINE appearances that month
    3  same source, same partie        scraping artefact
```

So neither raw sightings nor distinct months is right on its own:

```text
3 sources, 1 month, 1 partie    sightings 3   appearances 1
1 source,  3 months             sightings 3   appearances 3
1 source,  1 month, 2 parties   sightings 2   appearances 2
```

**`appearances` = count of distinct `(period, partie)`** — one exam sitting.
That is the honest measure of "how often the exam uses this question".

---

## 6. Selecting the set

Coverage curve for combined Tache 2:

| target | ranked by sightings | by distinct sittings | by distinct months |
|---|---|---|---|
| 50% | 251 | 257 | 288 |
| 60% | 350 | 359 | 402 |
| 70% | 482 | 490 | 549 |
| 80% | **669** | **685** | 725 |

The ranking measure barely changes the ordering; use `appearances` because it
is the correct definition, not because it moves the numbers.

**Open decision.** The original target was 80% of volume. That is ~670 clusters
for Tache 2 alone, and roughly double that with Tache 3 — a set most learners
will not finish, and an unfinished set cannot deliver the feeling of readiness
the product is aiming for. The tail is the reason: 628 of 1406 clusters were
seen exactly once.

Recommendation: **fix the set size to something studyable and publish the
coverage it achieves**, rather than fixing coverage and accepting the size.

> "300 questions per tache, covering 55% of reported exam volume"

is a promise that can be kept.

These numbers are provisional: reussir's committed JSONL is stale by ~600
questions, and the truncation fix in `scraper_reussir.py` changes 832 texts.
Recompute after the next full parse.

---

## 7. What this changes in the current code

```text
questions table          -> rename to `cluster` (it already IS one, mis-named)
raw_question table       -> new; does not exist today
Attempt.question_id      -> cluster_id, and its unique constraint
pair_decision            -> new
version / core_set_item  -> new
```

`Base.metadata.create_all` in `backend/app/main.py` only creates *missing*
tables — it never alters an existing one. Since the app is live on Neon, these
are migrations against real data, so **Alembic** starts here (it is already in
`backend/requirements.txt`).

---

## 8. Scope

**Phase 1 is single-source.** Nothing in this design needs to change for it.

`source` still stays on `raw_question`. Cross-source agreement is the strongest
ranking signal available — three independent sources reporting one question
beats one source reporting it three times — so keep collecting it even while
ranking ignores it.

Deferred:

- multi-source ranking
- the final set-size decision (§6)
- whether the LLM tier runs as a batch over the grey band or on demand behind
  `review.html`; `pair_decision` makes the two interchangeable
