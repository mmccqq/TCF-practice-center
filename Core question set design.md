# Core question set — design

Status: steps 1-3 implemented 2026-09-18 — all four tables created and
backfilled locally (revisions `e0dd5c5a1c06`, `dbbf7179181c`); steps 4-6
outstanding, and Neon has the step-1 schema only. Rewritten 2026-09-18,
replacing the 2026-09-04 draft.

The product goal: a bounded set of questions a user can work through and, on
finishing, feel prepared. This document defines the data model that makes such
a set stable enough to track progress against, and that lets the same corpus
be viewed two different ways — as a monthly timeline, and as a frequency-ranked
core set.

---

## 1. Why the current model does not work

Today there is one table, `questions`. `seed.py` groups every scraped sighting
by `(tache, fingerprint(text))`, keeps the newest sighting as the canonical
row, and stores the group size as `occurrences`.

That single table is being asked to be two incompatible things at once: an
identity ("this is one question") and a timeline entry ("this was asked in
September"). Three measured consequences:

**The canonical id moves, and the old row is never deleted.** When a question
recurs in a later month the group gains a newer sighting, so `collect()`
emits a different id. `seed.py` only inserts and updates, so the previous row
is left behind:

```text
run 1   group = {aug}         canonical = aug id   INSERT aug
run 2   group = {aug, sep}    canonical = sep id   INSERT sep
                                                   aug row is now an orphan
```

The live table currently holds **49** such cross-month duplicate pairs.

**Labels land on the wrong copy.** `load_labels.py` keys on question id, so a
label written against the August id stays on the orphan while the September
row shows unlabelled. Of those 49 groups, 43 have exactly one labelled row.

**History is thrown away.** Only half the corpus is single-month:

```text
 1 month   1359 ██████████████████████
 2 months   533 █████████
 3 months   303 █████
 4 months   208 ███
 5-8        288 █████
 9+          60 █          (one question has recurred across 18 months)
```

A recurring question is the strongest signal a candidate has, and global dedup
keeps only its last sighting.

---

## 2. The four layers

Identity, timeline and vocabulary are separated into their own tables. Each
answers one question and is the single source of truth for it.

```text
 layer 1            layer 2               layer 3            layer 4
 raw_questions      fingerprints          list_questions     core_subjects
 ───────────        ───────────           ─────────────      ─────────────   + themes
 one scraped        one unique text       one question       the controlled
 sighting           = the identity        per month          vocabulary
 provenance         labels live here      what the list      enforced by
 (internal)         attempts point here   page renders       foreign keys

 8,128 rows         2,751 rows            6,646 rows         99 + 16 rows
```

Table names are plural, matching the existing `users` / `questions` /
`attempts`.

**Task 2 and Task 3 share these tables**, separated by an indexed `tache`
column. The two tasks have identical shapes, so per-task tables would be the
same DDL written twice, two model classes each, and a third copy the day Task
1 is scraped. `tache` is already load-bearing rather than incidental — it is
part of `UNIQUE (tache, fingerprint)`, and `seed.py` has always grouped on
that pair.

The **vocabulary** is per task even so: `themes.tache` with
`UNIQUE (tache, name)` lets Task 3 have its own "Travel & tourism" with
entirely different core subjects under it. Without that column the two
vocabularies would collide on the first shared theme name. `core_subjects`
needs no `tache` of its own — it is implied by the theme it belongs to.

Dedup happens twice, on purpose, with two different keys:

```text
 global dedup      key = (tache, fingerprint)            -> layer 2
                   "what keeps coming back"                 the core set

 per-month dedup   key = (tache, period, fingerprint)    -> layer 3
                   "what was asked in September"            the timeline
```

Trying to serve both from one key is what produced every problem in §1.

---

## 3. Entities

### `raw_questions` — what a scraper found

| field | example | notes |
|---|---|---|
| `id` | `"0320260920401"` | PK. The scrapers' 13-digit id: source, year, month, tache, partie, sujet |
| `f_id` | → `fingerprints.id` | FK, **indexed**, not null |
| `tache` | `2` | indexed |
| `text` | | exactly as scraped, unnormalised |
| `period` | `"2026-09"` | |
| `partie` | `4` | which exam session within the month |
| `sujet` | `1` | |
| `source` | `"reussir"` | **internal only**, never returned by the API |
| `source_url` | | |

Never reaches a user. It exists so counts can be recomputed, layers 2 and 3
can be rebuilt from the database rather than from JSONL files on a laptop,
and "where did this come from" can be answered.

New in this design: the scraper output moves *into* the database. 8,128 rows
is negligible, and it is what makes every downstream layer reproducible.

### `fingerprints` — the question, and the only place labels live

| field | notes |
|---|---|
| `id` | PK, surrogate integer, **allocated once, permanent** |
| `fingerprint` | the sha256 from `deduplication.fingerprint()`, indexed |
| `tache` | 2 or 3. UNIQUE `(tache, fingerprint)` — the same wording under Task 2 and Task 3 is two questions, so the hash alone is not unique |
| `text` | canonical normalised wording, from the newest sighting |
| `theme_id` | → `themes.id`, FK, indexed, nullable |
| `abstract` | nullable |
| `core_subject_id` | → `core_subjects.id`, FK, indexed, nullable |
| `first_seen` | `"2025-03"`, derived |
| `last_seen` | `"2026-09"`, derived |
| `total_sightings` | lifetime count across all months, derived |
| `months_seen` | distinct periods, derived |

Everything user-facing that is *about the question rather than about one
month* points here: attempts, bookmarks, notes, labels.

`id` is a surrogate rather than the hash itself. The hash would be
reproducible with no allocation step, but it dead-ends at semantic merging:
when two differently-worded prompts are judged the same question, a
hash-as-id cannot express it. With a surrogate, merging is a pointer change
and no foreign key anywhere else moves. See §8.

### `list_questions` — what the list page renders

| field | notes |
|---|---|
| `id` | PK |
| | UNIQUE `(tache, period, f_id)` |
| `f_id` | → `fingerprints.id`, FK, indexed |
| `tache` | |
| `period` | `"2026-09"` |
| `month_sightings` | how many raw rows in *this* month collapsed here |
| `representative_raw_id` | → `raw_questions.id`, which sighting this row was built from |

Wholly derivable from layers 1 and 2: group `raw_questions` by
`(tache, period, f_id)`. It is materialised rather than a view so the list
page's pagination and ordering stay cheap. `ix_list_questions_tache_period`
is the index the list page reads it through.

The unique constraint is the design: the database itself guarantees one row
per question per month, rather than `seed.py` remembering to.

`id` must be **deterministic** — either the natural key `(tache, period,
f_id)` used directly, or a surrogate with the unique constraint above so a
rebuild is an upsert. A serial allocated at build time would hand the same
logical row a different id on every rebuild, breaking bookmarks. Implemented
as the surrogate; a full re-run of `backfill_questions.py` was verified to
change **0** of the 6,646 ids.

### `themes` and `core_subjects` — the controlled vocabulary, enforced

| `themes` | notes |
|---|---|
| `id` | PK |
| `tache` | 2 or 3, indexed |
| `name` | `"Travel & tourism"`. UNIQUE `(tache, name)`. 16 rows for Task 2 |

| `core_subjects` | notes |
|---|---|
| `id` | PK |
| `name` | `"low-budget weekend tour"` |
| `theme_id` | → `themes.id`, FK, indexed. The task is implied by this |
| | UNIQUE `(theme_id, name)`. 99 rows for Task 2 |

Theme is a table rather than a string on `fingerprints`, so a question's
theme and its core subject's theme cannot disagree — with two independent
strings, nothing would catch a question labelled *Travel & tourism* whose core
subject belongs to *Culture & entertainment*.

`llm_tasks.VOCABULARY` is **Task 2 only** — its prompt opens "You are
annotating TCF Canada Speaking Task 2 role-play prompts". Task 3 is an opinion
task and needs its own themes; `load_vocabulary.py` refuses `--tache 3` until
one is defined rather than silently reusing Task 2's. Every query it makes is
filtered by `tache`, so loading one task cannot disturb the other's rows.

`core_subjects` is keyed on `(theme_id, name)`, not `name` alone, because the
vocabulary in `llm_tasks.VOCABULARY` is defined per theme and the table should
mirror the structure the prompt already uses. The data agrees:
`"musical instrument"` currently appears under two different themes.

These tables earn their place as a **drift guard**. The vocabulary has 99
approved labels; the 587 labelled rows currently carry **138** distinct
core subjects. Forty crept in unapproved — the `films` / `film (single)`
problem, invisible today. As a foreign key, an unapproved label cannot be
written and adding one becomes a deliberate act.

Loaded by `backend/load_vocabulary.py --tache N`, which imports `VOCABULARY`
directly so the prompt and the database cannot disagree about what a valid
label is. It never deletes: a label dropped from the vocabulary is reported as
orphaned, not removed, because fingerprints may already point at it.

### `attempt` — progress

| field | notes |
|---|---|
| `user_id` | → `users.id` |
| `f_id` | → `fingerprints.id`, **not** `list_questions.id` |
| `practiced` | |
| `updated_at` | |
| | UNIQUE `(user_id, f_id)` |

Practising a question in the September section marks the same question tried
wherever else it appears. "Tried" means *tried this question*, not *tried this
month's copy of it* — and because `f_id` derives from text rather than from a
scraper id that can move, progress survives a re-scrape.

---

## 4. Rules this design commits to

**No list-valued columns.** Not `fingerprint.raw_ids`, not
`core_subject.fingerprints`. A list inside a column cannot be indexed into,
cannot carry a foreign key, must be rewritten whole to add one member, and —
worst — stores a fact that is already recorded from the other side, so the two
copies drift.

```text
 wrong   fingerprints.raw_ids        = [a, b, c]
 right   raw_questions.f_id          -> fingerprints.id    ... WHERE f_id = ?

 wrong   core_subjects.fingerprints  = [x, y, z]
 right   fingerprints.core_subject_id -> core_subjects.id  ... WHERE core_subject_id = ?
```

An index on the child column makes the reverse lookup as fast as reading an
array would have been, with one source of truth instead of two. A join table
is only needed if the relationship becomes many-to-many.

**Label the fingerprint, never the row.** `export_questions.py` deduplicates by
fingerprint before writing, so labelling stays at ~2,751 items rather than
6,646 — the LLM bill does not grow when per-month dedup triples the row count.
`load_labels.py` writes to layer 2, and every month's copy inherits identical
labels by construction rather than by luck.

**Two counts, named apart.** `list_questions.month_sightings` ("seen 3× in
September") and `fingerprints.total_sightings` ("asked in 18 months") are
different numbers. They are not both called `occurrences`, because a column
whose meaning quietly changed has already cost this project once: `source`
means "the newest sighting's source", not provenance.

**Deletes are explicit.** A rebuild removes rows that are no longer produced,
after transferring anything attached to them. The current pipeline never
deletes, which is the direct cause of the 49 orphans.

---

## 5. What each layer answers

```sql
-- the list page: September, newest month first
SELECT lq.*, f.text, t.name AS theme, cs.name AS core_subject
FROM   list_questions lq
JOIN   fingerprints  f  ON f.id = lq.f_id
LEFT   JOIN themes        t  ON t.id  = f.theme_id
LEFT   JOIN core_subjects cs ON cs.id = f.core_subject_id
WHERE  lq.tache = 2
ORDER  BY lq.period DESC;

-- the high-frequency core set: no second pipeline, just layer 2
SELECT text, total_sightings, months_seen, last_seen
FROM   fingerprints
WHERE  tache = 2
ORDER  BY total_sightings DESC
LIMIT  100;

-- "also asked in" on a card
SELECT period FROM list_questions WHERE f_id = ? ORDER BY period DESC;

-- has this user tried it, in any month?
SELECT 1 FROM attempt WHERE user_id = ? AND f_id = ?;

-- vocabulary drift, now answerable
SELECT name FROM core_subjects WHERE id NOT IN (SELECT core_subject_id FROM fingerprints);
```

---

## 6. Sizes

| table | Task 2 | Task 3 | total | note |
|---|---|---|---|---|
| `raw_questions` | 4,231 | 3,897 | 8,128 | one per scraped sighting |
| `fingerprints` | 1,512 | 1,239 | 2,751 | unique texts, tier-1 dedup |
| `list_questions` | 3,463 | 3,183 | 6,646 | +142% against today's single table |
| `core_subjects` | 99 | — | 99 | Task 3 vocabulary not yet defined |
| `themes` | 16 | — | 16 | |

The row growth is irrelevant for storage. It would have been a 2.4× labelling
bill if labels lived on layer 3, which is precisely why they do not.

---

## 7. Migration order

Expand/contract, so the deployed site keeps serving throughout. Steps 1-3 are
additive and reversible; the first user-visible change is step 4.

1. **Done 2026-09-18.** Create `themes`, `core_subjects`, `fingerprints`,
   `raw_questions` (revision `e0dd5c5a1c06`) and load Task 2's vocabulary with
   `load_vocabulary.py`. Nothing reads them. Also: SQLite foreign-key
   enforcement turned on in `app/db.py`, without which the step-2 backfill
   would be tested against a database that ignores the constraints it relies
   on.
2. **Done 2026-09-18.** Backfill `fingerprints` and `raw_questions` from the
   scraper JSONL with `backfill_questions.py`. Landed on 8,128 raw and 2,751
   fingerprints (1,512 Task 2, 1,239 Task 3).
3. **Done 2026-09-18.** Create `list_questions` (revision `dbbf7179181c`) and
   backfill it in the same pass. Landed on 6,646 rows (3,463 + 3,183). Of the
   2,861 `questions` rows, 2,816 map to exactly one list row and none map to
   more than one; the other 45 hold text that predates the scraper fixes (the
   `→` marker and the multi-`<strong>` truncation) and so no longer hash to
   anything the scrapers produce. 40 of those 45 are labelled, which the
   label-transfer step has to account for.
4. Point the API at `list_questions` joined to `fingerprints`. The site now
   serves per-month rows with consistent labels.
5. Migrate `attempt.question_id` → `attempt.f_id`: add the column, dual-write,
   backfill, drop the old one.
6. Drop `questions` once nothing references it.

Each backfill runs against local SQLite with a dry run first, then Neon.

---

## 8. Deliberately deferred

**Semantic merging (tier 3).** Layer 2 is the cluster table from the previous
draft, arriving with only tier-1 (exact fingerprint) merging switched on. When
embeddings or TF-IDF judge two differently-worded prompts to be one question,
the change is contained: a `canonical_id` self-reference on `fingerprints`, or
a members table. Labels and attempts already point at layer 2, so nothing else
moves. This is the whole reason `f_id` is a surrogate.

**Versioning of question text.** The previous draft had a `version` entity for
immutable frozen wording. Nothing currently needs it: `fingerprint.text` is the
current best wording and no feature yet depends on it never changing.
Reintroduce it when one does.

**Multiple core subjects per question.** Modelled as one FK today. If a
question genuinely needs several, it becomes a `fingerprint_core_subjects`
join table — not an array column.

**Task 3 labelling.** Its themes and core subjects do not exist yet;
`llm_tasks.VOCABULARY` covers Task 2 only. `themes` holds Task 2's 16 rows and
waits for Task 3's.

**Named foreign-key constraints.** The step-1 migration leaves the FK
constraints unnamed, so the database assigns them. Fine while the tables are
only ever created, but dropping or altering one later means looking its name
up first. A `naming_convention` on `Base.metadata` would fix it for future
migrations; it is not applied yet because it would make the existing tables'
constraints differ from what the convention describes.
