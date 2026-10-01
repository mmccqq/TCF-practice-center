#!/usr/bin/env python3
"""
What to ask the model, kept apart from how the request is made.

A Task owns the prompt, the answer field and how a batch of rows becomes one
user message. Everything else - chunking, the n-alignment check, resume,
providers, batching - lives in llm.py and is identical for every task.

Adding a task means adding a Task() here and nothing else:

    python3 llm.py sync questions_reussir/tache2.jsonl --task theme

The single most important rule in this file: the prompt's worked example and
the JSON schema must agree, because a provider offering JSON *mode* rather
than schema enforcement will follow the example. Task.schema() and
Task.example() are both generated from `answer_key`, so they cannot drift -
never hand-write the example block into a prompt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

# The wrapper object every task's reply is delivered in. Task-neutral on
# purpose: the engine reads this one key regardless of which task ran.
ENVELOPE = "answers"


@dataclass
class Task:
    """One thing to ask the model for, over a batch of numbered rows."""

    name: str
    #: the standing instructions. Do NOT include a worked JSON example - the
    #: envelope, the key and the example are generated from `answer_key`.
    rules: str
    #: the field each answer object carries on the wire
    answer_key: str
    #: what that field should contain, shown to the model in the schema
    answer_description: str
    #: the field name written to the output JSONL; defaults to answer_key
    output_field: str = ""
    #: three (input, answer) pairs used to build the worked example
    examples: list[tuple[str, str]] = field(default_factory=list)
    #: a row field that must not be mixed within one request. llm.py splits on
    #: it before chunking, so prompt_for() can rely on every row in a chunk
    #: sharing the value - which is what lets the prompt vary per group.
    group_by: str | None = None

    def __post_init__(self) -> None:
        self.output_field = self.output_field or self.answer_key

    # -- what the model is sent ------------------------------------------
    def body(self, rows: list[dict]) -> str:
        """Rows as the model sees them: 1-based, one per line.

        Override for a task whose unit is not a single question - a
        pairwise duplicate judgement, say, would render two texts per number.
        """
        return "\n".join(f"{i}. {r['text']}" for i, r in enumerate(rows, 1))

    def example(self) -> str:
        """The worked example, generated so it always matches the schema."""
        if not self.examples:
            return ""
        shown = [{"n": i, self.answer_key: a}
                 for i, (_q, a) in enumerate(self.examples, 1)]
        return ("\n\nExemple d'entree:\n\n"
                + "\n".join(f"{i}. {q}" for i, (q, _a) in enumerate(self.examples, 1))
                + "\n\nSortie correspondante:\n\n"
                + json.dumps({ENVELOPE: shown}, ensure_ascii=False, indent=1))

    #: Appended to every prompt. DeepSeek rejects `response_format=json_object`
    #: unless the literal word "json" appears somewhere in the prompt - a
    #: substring check, not an understanding of the request:
    #:
    #:   400 Prompt must contain the word 'json' in some form to use
    #:       'response_format' of type 'json_object'
    #:
    #: A worked example made of JSON does not satisfy it. Rather than leaving
    #: each task's rules to remember the word - core_subject did not, and only
    #: failed on the one provider that checks - it is added here, once, for all
    #: of them. Harmless on providers that do not care, and harmless to repeat
    #: for the tasks whose rules already say it.
    JSON_NOTE = ("\n\nRepondez uniquement avec un objet JSON de la forme montree "
                 "ci-dessus.")

    @property
    def prompt(self) -> str:
        return self.rules.rstrip() + self.example() + self.JSON_NOTE + "\n"

    def prompt_for(self, rows: list[dict]) -> str:
        """The prompt for one chunk. Constant unless a task overrides it.

        Only tasks with `group_by` set should vary this, because only then is
        every row in the chunk guaranteed to share the grouping value.
        """
        return self.prompt

    def sample_group(self) -> str:
        """A group value this task accepts. Used only by llm.py's selftest,
        which cannot know what a valid one looks like."""
        return "G"

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                ENVELOPE: {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "n": {"type": "integer",
                                  "description": "the number the row was given in the request"},
                            self.answer_key: {"type": "string",
                                              "description": self.answer_description},
                        },
                        "required": ["n", self.answer_key],
                        "additionalProperties": False,
                    },
                },
            },
            "required": [ENVELOPE],
            "additionalProperties": False,
        }

    # -- what comes back --------------------------------------------------
    def record(self, row_id: str, answer: str, model: str) -> dict:
        return {"id": row_id, self.output_field: answer, "model": model}


RULES_THEME = """\
You classify French TCF Canada Tâche 2 role-play prompts by theme. Each prompt has a role for the examiner (je), a role for the candidate (tu, vous), and a situation. 
The candidate needs to get information about a subject. Identify this subject, and classify it with one of the themes below.
Focus on the last part of the prompt which indicates the main subject.

Travel & tourism: Is it travel, trip the subject? Including tour plans, city sightseeing, someone's recent trip, getting to know a city. Excludes: language stay → Education & courses. how to travel such as taking an airline → Transport & mobility. A trip taken for sport → Sports & fitness. A flat renting out is housing & surroundings.
Culture & entertainment: Are a specific cultural place or one-time public activity the subject? Places like museums, libraries, theme parks, zoos and nature parks, board-game clubs, or activities like public festivals, concerts, shows, local customs and how people spend their evenings. Excludes: The content of a film or book → Media & reading; a municipal arts class → Education & courses
Food & dining: Is knowing a restaurant or food, meal the subject? Includes: organize a party when a specific restaurant is the subject. Excludes: A cookery or baking class → Education & courses; opening ceremony → Social life & event.
Social life & event: Is the activity a social event that makes connections with other people the subject? Building and neighbourhood get-togethers, meeting new people in a city, weddings and parties as social events. Excludes: personal info -> personal info & experience.
Housing & surroundings: Is the house or surrounding/community condition for living the subject? Renting or buying a home, flatshares, neighbourhood choice for moving, landlords, estate agents when housing is the subject. 
Sports & fitness: Is sport the subject? Includes sport club. Learning a sport → Education & courses
Tasks, work & career: Is jobs, tasks, working conditions the subject? including favors like xxx-sitting, airport pickups.
Transport & mobility: Is the subject how people travel or get around? carpooling, cycling, bike and car hire (including a bike hired for sightseeing while on holiday).
Education & courses covers: Is education the subject?  any course, workshop or learning stays, or questions related to school or child education. Child-sitting tasks → tasks, work & career
Shopping & consumer services: Buying and selling goods, second-hand furniture, shops, deliveries, producers selling direct
Personal info & experience: Is knowing a person or the settling experience the subject and purpose? This includes buying someone a gift. Excludes: Questions confined to one domain: one job → Work & career 
Media & reading: Is the content of films, series, books, blogs the subject? Television shows → Culture & entertainment.
Volunteering & associations covers: Charities and voluntary associations, unpaid community. work excludes: Paid community or sports work → the relevant theme
Health & public services: includes healthcare systems, doctors, clinics, medication, sick leave
Others: None of the above.

---

## OUTPUT

Return a JSON array. Exactly one object per input prompt, with the same `n`,
in the same order. Do not omit, merge, or add entries.
"""


THEME = Task(
    name="theme",
    answer_key="theme",
    answer_description="exactly one of the themes defined in the rules",
    rules=RULES_THEME,
    examples=[
        ("Je travaille a l'accueil d'un club sportif de la ville. Vous envisagez "
         "de vous inscrire et vous me posez des questions (horaires, types de "
         "cours, tarifs, etc.).", "Sports & fitness"),
        ("Je suis votre voisin(e). Je m'absente en vacances et je cherche "
         "quelqu'un pour s'occuper de mon animal. Vous voulez en savoir plus "
         "avant d'accepter (dates, soins, regles, etc.).", "Tasks, work & career"),
        ("Je suis un(e) collegue. J'ai participe a un mariage ce week-end. Vous "
         "voulez savoir comment s'est deroulee la ceremonie (repas, lieu, "
         "ambiance, etc.).", "Social life & event"),
    ],
)



RULES_ABSTRACT = """\
Each prompt describes a role-play situation with:
a role for the examiner
a role for the candidate
a topic that the candidate must ask about
Most prompts use generic wording such as “Je suis votre ami(e)... Vous me posez des questions...”. Ignore this generic structure. Only summarize the specific situation/topic that the candidate needs to ask about.

For each numbered prompt:
Write exactly one noun phrase in English that names the situation.
Use 8 words maximum.
Do not use conjugated verbs.
indicate past tense if the topic is about past experience, for example, a wedding a friend attended
use organize if the candidate is about organizing an event. for example, organize a wedding
Do not use “vous”.
Do not add punctuation at the end.
Make the phrase specific enough to distinguish the prompt from other prompts.
Do not add information that is not present in the original prompt.

Output format
Return a valid JSON array.
For every input prompt, output exactly one object with:
n: the original prompt number
abstract: the English noun phrase

Preserve the original order.

Do not:
omit any prompt
merge prompts
add prompts
change the n value
include explanations or additional fields
output Markdown or code fences

Example
Input:
12. Je suis votre ami(e) et je viens de commencer un nouveau travail. Vous me posez des questions sur mon emploi, mes horaires et mon lieu de travail.
Output:
[{"n":12,"abstract":"Nouveau travail"}]."""

ABSTRACT = Task(
    name="abstract",
    answer_key="abstract",
    answer_description="English noun phrase, at most 8 words, no final punctuation",
    rules=RULES_ABSTRACT,
    examples=[
        ("Je suis votre voisin(e). Vous venez d'arriver dans la ville et vous ne "
         "connaissez personne. Vous me demandez comment faire pour rencontrer de "
         "nouvelles personnes.", "meeting new people"),
        ("Je travaille dans une agence de location de voitures, vous avez besoin "
         "d'en louer une. Posez-moi des questions sur les conditions (prix, "
         "duree, assurance, etc.).", "rent a car"),
        ("Je suis votre voisin(e). Je m'absente en vacances et je cherche quelqu'un pour s'occuper de mon animal. Vous voulez en savoir plus avant d'accepter (dates, soins, règles, etc.). ",
         "pet-sitting"),
    ],
)


# ---------------------------------------------------------------------------
# core_subject: a finer label under the theme, drawn from a controlled list.
#
# Unlike the other tasks the prompt is NOT constant - each theme has its own
# vocabulary, and sending only the relevant one keeps the model reusing labels
# instead of inventing near-synonyms ("renting a car" / "car rental" / "car
# hire"). `group_by = "theme"` is what makes that safe: llm.py guarantees every
# row in a chunk shares a theme, so prompt_for() can look one up.
#
# FILL THIS IN. One entry per theme, exactly as the theme is spelled in the
# database - CoreSubjectTask.prompt_for() raises on an unknown theme rather
# than quietly falling back, because a subject drawn from the wrong theme's
# list is worse than no subject at all.
#
# A theme mapped to an empty list means "no vocabulary yet": the model is told
# to propose a label and flag it, which is the bootstrap for a theme you have
# not curated.
VOCABULARY: dict[str, list[str]] = {
    "Culture & entertainment": [
        "amusement park",
        "artistic activity",
        "board game club",
        "bookshop game",
        "borrowing books",
        "Canadian evening activities",
        "concert",
        "film festival",
        "library",
        "low-budget cultural activity",
        "museums",
        "music festival",
        "New Year celebrations",
        "television show",
        "toy library",
    ],
    "Education & courses": [
        "after-school activities",
        "artistic lessons",
        "baking lessons",
        "child workshop",
        "community lessons",
        "continuing studies",
        "cooking lessons",
        "dance lessons",
        "drawing lessons",
        "enrollment",
        "language lessons",
        "language stays",
        "music lessons",
        "piano lessons",
        "schools",
        "swimming lessons",
        "university course",
    ],
    "Food & dining": [
        "home cooking services",
        "preparing a meal",
        "restaurant",
    ],
    "Health & public services": [
        "doctor",
        "healthcare system",
    ],
    "Housing & surroundings": [
        "buying a house",
        "neighborhood",
        "renting accommodation",
        "renting out for holidays",
        "shared accommodation",
    ],
    "Media & reading": [
        "blog",
        "book",
        "films",
        "films in cinema",
        "series",
    ],
    "Personal info & experience": [
        "gift",
        "personal life",
        "settling experience",
        "student's activities",
        "weekend activities",
    ],
    "Shopping & consumer services": [
        "costume rental",
        "grocery delivery",
        "selling furniture",
        "shopping options",
        "smartphone",
    ],
    "Social life & event": [
        "birthday party",
        "friends visiting",
        "gardening activities",
        "meeting new people",
        "neighbour gathering",
        "outings plateform",
        "restaurant opening event",
        "retirement party",
        "school party", 
        "wedding",
    ],
    "Sports & fitness": [
        "competition and training",
        "jogging outings",
        "sport club",
        "swimming pool",
        "training sessions",
    ],
    "Tasks, work & career": [
        "after-school childcare job",
        "airport pickup",
        "baby-sitting",
        "change work",
        "child escort",
        "delivery",
        "house-sitting",
        "household job",
        "interview",
        "moving house",
        "pet-sitting",
        "remote working setup",
        "restaurant job",
        "Running a Shop",
        "work condition",
    ],
    "Transport & mobility": [
        "air travel",
        "bicycle rental",
        "car rental",
        "carpooling",
        "commuting by bike",
        "public transport",
    ],
    "Travel & tourism": [
        "accommodation options",
        "child's vacation",
        "city trip",
        "country trip",
        "countryside trip",
        "cruise",
        "destination comparison",
        "excursion",
        "family trip",
        "favorite city",
        "holiday trip",
        "hotel",
        "low-budget weekend trip",
        "renting chalet",
        "seaside holiday",
        "ski resort holidays",
        "weekend trip",
    ],
    "Volunteering & associations": [
        "Volunteering & associations",
    ],
}


# How to tell apart labels that keep getting confused, keyed like VOCABULARY.
#
# One place, read by every labeller: llm.py's core_subject prompt lists these
# under the vocabulary, jev_labels.py passes them to Jev as each option's
# description, and the agent shows them beside each subject. A label without a
# note is described by its own name, which is enough for most of them - add a
# note when a pair keeps turning up in disagreements, not before.
SUBJECT_NOTES: dict[str, dict[str, str]] = {
    # For these the interlocutor's role IS the clue - an exception to the
    # METHOD's "ignore the role", which the note is allowed to override.
    "Sports & fitness": {
        "sport club": "an organisation's offer: at the desk of a club or sports "
                      "centre (club sportif, centre sportif, centre culturel et "
                      "sportif), the candidate wants to register and choose "
                      "among what it offers - sports available, prices, "
                      "schedules.",
        "training sessions": "joining someone's own sport: a group they lead "
                             "(in an association or a community centre) or a "
                             "partner they are looking for. The candidate asks "
                             "how it goes - type of activity or session, "
                             "schedule, level, participants. When the activity "
                             "is jogging or running outings, use jogging "
                             "outings instead.",
    },
    "Media & reading": {
        "films": "the film itself - genre, actors, story, duration, opinion. "
                 "Schedule and price are not part of it. Use this even when the "
                 "candidate plans to see the film, and even if a price is "
                 "mentioned, as long as no cinema is.",
        "films in cinema": "going to see a film at a cinema: the prompt mentions "
                           "the cinema AND asks about showtimes or prices "
                           "(horaires, séances, films à l'affiche, prix).",
    },    "Travel & tourism": {
        "destination comparison":"when candidate needs to compare different destination",
        "holiday trip": "a typical vacation with no country and no city named - "
                        "planning holidays in general, e.g. at a travel agency "
                        "(places to visit, accommodation, cost).",
        "city trip": "a trip to one city: a city is named or clearly meant "
                     "(ma ville, votre ville, une ville que je connais). This "
                     "includes visiting someone where they live - \"I have lived "
                     "in Canada for years, you plan to visit me\" is a trip to "
                     "that person's city, even though only the country is named.",
        "country trip": "a trip around a country, a region of it, or countries in general"
                        "(e.g. a stay or a tour in Canada) - no city, and not a "
                        "visit to someone's home there.",
    },
}


def subject_notes(theme: str) -> dict[str, str]:
    """The notes for one theme, limited to labels its vocabulary still has -
    a note for a label that was renamed or removed must not reappear as an
    option under its old name."""
    allowed = set(VOCABULARY.get(theme, []))
    return {k: v for k, v in SUBJECT_NOTES.get(theme, {}).items() if k in allowed}


RULES_CORE_SUBJECT = """\
You are annotating TCF Canada Speaking Task 2 role-play prompts (in French).

For each numbered sujet, extract the CORE SUBJECT: the subject that the
candidate must request information about.

METHOD
1. Find the request clause: "vous me demandez / vous me posez des questions /
   vous souhaitez vous renseigner sur ...". The grammatical object of that
   clause is the core subject.
2. IGNORE the interlocutor's role ("Je suis votre voisin(e)...", "Je travaille
   dans...") unless it is the only clue to the topic.
3. If the scenario is about someone's personal experience of X, the subject is
   X, not "experience".

OUTPUT FORMAT
- Reuse a label from the CONTROLLED_VOCABULARY below whenever one fits
  semantically, even if the wording differs. Only create a new label if none
  of them fits.
- a new lable is a short English noun phrase, 1-4 words. No articles, no verbs, no gerund unless unavoidable ("pet-sitting" ok).
- Every sujet in one request shares a theme, so the vocabulary shown is the
  one for that theme. The worked example further down illustrates the format
  only; its labels may come from a different theme's list.
"""


@dataclass
class CoreSubjectTask(Task):
    """A Task whose vocabulary is chosen per theme."""

    def sample_group(self) -> str:
        return next(iter(VOCABULARY))

    def prompt_for(self, rows: list[dict]) -> str:
        themes = {r.get("theme") for r in rows}
        if len(themes) != 1:
            raise ValueError(f"a chunk mixes themes {sorted(map(str, themes))} - "
                             f"group_by should have prevented this")
        theme = themes.pop()
        if theme not in VOCABULARY:
            raise ValueError(
                f"no vocabulary for theme {theme!r}. Add it to VOCABULARY in "
                f"llm_tasks.py, or exclude these rows - guessing a subject "
                f"from another theme's list is worse than leaving it empty.")

        allowed = VOCABULARY[theme]
        if allowed:
            block = (f"\n\nCONTROLLED_VOCABULARY for the theme {theme!r}.\n"
                     f"Reuse one of these whenever it fits, even if the wording "
                     f"differs. Only invent a label if none fits, and set "
                     f"new_label accordingly.\n["
                     + "\n".join(allowed) + "]")
            notes = subject_notes(theme)
            if notes:
                block += ("\n\nHOW TO TELL THESE APART - the label is the text "
                          "before the colon:\n"
                          + "\n".join(f"- {k}: {v}" for k, v in notes.items()))
        else:
            block = (f"\n\nThere is no vocabulary yet for the theme {theme!r}. "
                     f"Propose a short label and treat every one as new.")
        # same tail as Task.prompt - this override rebuilds the prompt rather
        # than extending it, so the JSON note has to be repeated here or it is
        # lost exactly for the task that already forgot to mention JSON
        return self.rules.rstrip() + block + self.example() + self.JSON_NOTE + "\n"


CORE_SUBJECT = CoreSubjectTask(
    name="core_subject",
    answer_key="core_subject",
    answer_description="short English noun phrase, 1-4 words, from the theme's "
                       "controlled vocabulary when one fits",
    rules=RULES_CORE_SUBJECT,
    group_by="theme",
    # Three real questions and the label each should get. These are what the
    # model actually imitates, so they matter more than the rules above: each
    # one demonstrates a METHOD step rather than just being a correct answer.
    examples=[
        # rule 1 + 3: the request clause is "des questions sur l'hotel", and
        # the parenthetical is aspects, not the subject
        ("Vous etes a l'hotel pour quelques jours. Je suis responsable de "
         "l'accueil. Vous me posez des questions pour organiser votre sejour "
         "(services, horaires, restauration, etc.).",
         "hotel"),
        # rule 4: the friend's experience is the frame; the subject is the
        # thing itself - pet-sitting, not "someone's experience of pet-sitting"
        ("Je suis votre ami(e). Je cherche une personne pour garder mon animal "
         "pendant mes vacances. Vous me posez des questions pour savoir si "
         "vous pouvez le faire (dates, soins, regles, etc.).",
         "pet-sitting"),
    ],
)


TASKS: dict[str, Task] = {t.name: t for t in (THEME, ABSTRACT, CORE_SUBJECT)}
