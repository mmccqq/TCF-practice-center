This is a personal project for my resume. Daily unique visitors will likely stay under 1,000.

Elevator pitch

A focused preparation platform for the TCF Canada exam, built around real past questions rather than generic French practice. It combines a searchable question bank covering listening, reading, writing, and speaking with a spaced-repetition flashcard system and a reusable material library, so candidates can move from "I've seen this question" to "I can answer this question under time pressure."

Real exam question bank

The core of the platform is an archive of past TCF Canada questions across all four sections. The deepest coverage is in speaking, where Tasks 2 and 3 carry the most preparation value:

High-frequency banks — questions filtered to the last 6 months and last 12 months, so candidates can prioritize what is statistically most likely to appear.
Full bank — the complete archive with search and filtering.
Theme classification — questions grouped by topic (work, travel, life in Canada for Task 2; education, immigration, technology for Task 3) so candidates can prepare by theme instead of question by question.
Answer strategies — each question links to a suggested approach and, for Task 3, a full model response.
Progress tracking — completion state at the individual question level, visible across both the high-frequency and full banks.

Material library

Speaking preparation depends on reusable building blocks, not memorized answers. The library stores question templates and answer structures for Task 2, and templates plus supporting material for Task 3.

Every item is available in two layers: an official library curated by the platform, and a personal library. Editing an official item automatically forks it into the user's personal library, so candidates can adapt material to their own life and vocabulary without losing the original. Task 3 material supports sentence-by-sentence playback for shadowing practice.

Flashcards with spaced repetition

A built-in Anki-style review system. Any question, strategy, or piece of source material can be pushed into a deck with one action, which keeps the study loop inside the platform instead of splitting it across tools.

Create, delete, and manage decks, with configurable daily review limits
Edit cards directly; audio available via text-to-speech
Scheduling driven by a spaced-repetition algorithm

Accounts and progress

Sign-up and login, including Google sign-in, with password management. Each account carries a progress dashboard covering speaking Tasks 2 and 3 plus flashcard review status, and links directly into the user's personal material library.

Admin and content operations

The question bank is not hand-written, and keeping it accurate is most of the work. An admin area inside the platform — gated on an account flag rather than run as a separate application — covers the whole curation loop.

Vocabulary management. Themes and their controlled vocabularies are stored as tables with foreign keys, so a label nobody approved cannot be attached to a question. Each label is shown with how many questions use it, sssssssssssswhich is what exposes dead entries and near-duplicates. Labels can be added, renamed, moved between themes, and merged; merging repoints every affected question and then removes the old label. Deleting a label that is still in use is refused, so no action can silently strip labels from questions.

Labelling workspace. Questions can be filtered to those missing a theme, core subject, or abstract, then edited in place or assigned in bulk. Core subjects are scoped to their theme, so an inconsistent pairing cannot be saved at all.

Review queue. Output from an LLM labelling run is uploaded and adjudicated one item at a time: take a model's answer, type a different one, or skip. Decisions are stored server-side, so a review survives a cleared browser and can be finished on another machine. Applying a batch writes straight to the question bank and reports whatever it could not resolve — usually a label that is not in the vocabulary yet — rather than failing or inventing one.

Model comparison. Two or more labelling runs can be compared for agreement, with disagreements grouped by label pair and by which labels are contested most often. Agreement is used as triage rather than as a measure of accuracy: where every model agrees the answer is usually right and needs no human, and where they split is where review time is worth spending. Disagreements convert into a review queue in one action.

The system ingests model output rather than calling model APIs itself. Labelling runs stay on a workstation where prompts and spend are under direct control, and the server holds no provider credentials.

Behind the scenes

Question data is collected from public TCF Canada practice sources, then cleaned and processed: duplicate detection, frequency counts, and LLM-assisted theme and topic classification against a controlled vocabulary.

The data model separates a scraped sighting from the question it reports. Every sighting is kept; identical questions collapse into one record that owns the labels and the user's progress; a third layer lists each question once per month it appeared in. That separation is what lets the same question show up across eighteen months of the archive while carrying one set of labels and one "practised" state — and it is what makes a frequency ranking honest, since a question two sources both reported in the same month counts once, not twice.