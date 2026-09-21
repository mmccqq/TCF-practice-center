This is a personal project for my resume. Daily unique visitors will likely stay under 1,000.

Elevator pitch

A focused preparation platform for the TCF Canada exam, built around real past questions rather than generic French practice. It combines a searchable question bank covering listening, reading, writing, and speaking with a spaced-repetition flashcard system and a reusable material library, so candidates can move from "I've seen this question" to "I can answer this question under time pressure."

Real exam question bank

The core of the platform is an archive of past TCF Canada questions across all four sections. The deepest coverage is in speaking, where Tasks 2 and 3 carry the most preparation value.

Oral core set — the recommended path, and the platform's main claim. Questions are grouped into core subjects, each subject ranked by how many distinct exam months it has actually come up in. Around 2,750 unique questions reduce to roughly 90 subjects, each shown by its most-asked question with the rest one click away. Frequency is counted in months rather than in reports, so a question two sources both filed for the same sitting counts once — the number a candidate sees is the number of times the exam asked.

Full bank — the complete archive, grouped into month sections, with search and theme filtering. A question that recurred across eighteen months appears in each of those months while carrying one set of labels and one practised state.

Theme classification — every question carries a theme and a finer core subject drawn from a controlled vocabulary, so candidates can prepare by topic instead of question by question.

Progress tracking — a practised tick and a bookmark star on every question, both keyed to the question itself rather than to one month's copy. Marking a question practised in September marks it everywhere it appears, and progress is shown per theme and across the whole core set so "am I done with this topic" has an answer.

Bookmarks — a saved list per task, newest first, for questions worth returning to.

Answer strategies — each question links to a suggested approach and, for Task 3, a full model response.

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

The question bank is not hand-written, and keeping it accurate is most of the work. An admin area inside the platform — gated on an account flag rather than run as a separate application — covers the whole pipeline, from scraping to a labelled question, without leaving the browser.

Intake. One action checks each source for exam months the bank does not have, downloads only those pages, parses them in memory, and loads three layers: every scraped sighting, the deduplicated questions those sightings collapse into, and one row per month each question appeared in. No HTML is stored, because the database already records which months are in — so a routine run fetches a page or two rather than an archive, and works on a host whose filesystem is wiped between restarts.

Labelling runs. A labelling run is started from the questions themselves: filter to what is missing a theme, select them, choose a task, model and batch size, and start. Runs execute on the server and are watched from a job list with live progress, timings, cancellation, and a link to the results. A run that dies — a deploy, an idle host — is reported as interrupted rather than left looking alive, and whatever it completed is still reviewable. A per-job question ceiling bounds what one mistyped filter can spend.

Vocabulary management. Themes and their controlled vocabularies are stored as tables with foreign keys, so a label nobody approved cannot be attached to a question. Each label is shown with how many questions use it, which is what exposes dead entries and near-duplicates. Labels can be added, renamed, moved between themes, and merged; merging repoints every affected question and then removes the old label. Deleting a label that is still in use is refused, so no action can silently strip labels from questions.

Labelling workspace. Questions can be filtered to those missing a theme, core subject, or abstract, then edited in place or assigned in bulk. Core subjects are scoped to their theme, so an inconsistent pairing cannot be saved at all.

Review queue. Answers are adjudicated one item at a time: take a model's answer, pick an existing label from the question's theme, type something new, or skip. Each candidate shows how many questions already use it, which is what separates an established label from a near-duplicate a model has just invented. Decisions are stored server-side, so a review survives a cleared browser and can be finished on another machine. Applying a batch writes straight to the question bank and reports whatever it could not resolve — usually a label not yet in the vocabulary — with a one-click option to add it and apply again, rather than failing or inventing one.

Model comparison. A run can ask two models instead of one. Both sets of answers land in a single batch, so where the models agree the answers can be accepted in one action and only the disagreements need a person. Agreement is treated as triage rather than as a measure of accuracy: where every model agrees the answer is usually right, and where they split is where review time is worth spending. Disagreements are grouped by label pair and by which labels are contested most often — a label appearing repeatedly there is one whose boundary against a neighbour needs redrawing, which is a prompt fix rather than a per-question one.

Because the agreed answers and the adjudicated ones live in the same batch, a comparison is applied in a single pass. Loading one model's output, comparing, and then loading the resolved disagreements — three passes over the same questions — is the workflow this replaces.

Export. Any filtered view of the bank downloads as a spreadsheet with every field, for analysis outside the platform.

Behind the scenes

Question data is collected from public TCF Canada practice sources, then cleaned and processed: duplicate detection, frequency counts, and LLM-assisted theme and topic classification against a controlled vocabulary.

The data model separates a scraped sighting from the question it reports. Every sighting is kept; identical questions collapse into one record that owns the labels and the user's progress; a third layer lists each question once per month it appeared in. That separation is what lets the same question show up across eighteen months of the archive while carrying one set of labels and one "practised" state — and it is what makes a frequency ranking honest, since a question two sources both reported in the same month counts once, not twice.