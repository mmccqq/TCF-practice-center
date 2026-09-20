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
You classify French TCF Canada Tâche 2 role-play prompts by theme. Each prompt describes a situation: a role for the examiner(Je), a role for the candidate(Tu, vous), and candidate need to ask questions to get information about this situation. Almost all of them share the same structure (“I am your friend... You ask me questions...”).
Read the situation, identify the subject and classify it using the themes below.
The parenthetical hint list at the end of each prompt is the strongest signal available for finding a subject. 
When two rules seem to fit, decide by asking which rule the HINTS point to, not the narrative.
THEME (17 values):
Travel & tourism: Is it travel, trip the subject? Including tour plans, city sightseeing, someone's recent trip, getting to know a city, language trip. does not cover: A trip taken for sport → Sports & fitness. a flat renting out is housing & real estate.
Culture & entertainment: Are a specific cultural place or one-time public activity the subject? Places like Museums, libraries, cinemas as venues, theme parks, zoos and nature parks, board-game clubs, or activities like public festivals, concerts, shows, local customs and how people spend their evenings. does not cover: The content of a film or book → Media & reading; a municipal arts class → Education & courses
Food & dining: Is knowing a restaurant or food, meal the subject? does not cover: A cookery or baking class → Education & courses; booking a venue for a party where food is incidental → Community & social life
Social life & event: Is the activity a social event that makes connections with other people the subject? Building and neighbourhood get-togethers, meeting new people in a city, weddings and parties as social events. does not cover: Anything centred on children or school → children & school, personal info -> getting to know a person.
Housing & surroundings: Is the house or surrounding/community condition for living the subject? Renting or buying a home, flatshares, neighbourhood choice for moving, landlords, estate agents when housing is the subject. 
Sports & fitness: Is sport the subject? Learning a sport goes to Education & courses
Tasks, work & career: Is jobs, tasks, working condition the subject? including favors like xxx-sitting, airport pickups.
Transport & mobility: Public transport, carpooling, cycling as a way of getting around, bike and car hire (including a bike hired for sightseeing while on holiday).
Education & courses covers: Is learn something the subject?  any course, or workshop, whatever subject it teaches — cooking, swimming private tuition, municipal art classes  or the questions are about enrolling. The parenthetical hints are about fees and schedule.
Shopping & consumer services: Buying and selling goods, second-hand furniture, shops, deliveries, producers selling direct, hire of objects
Getting to know a person: Is knowing a person the subject and purpose? their routine, family and hobbies in general, their preference and personality. This includes buying someone a gift. does not cover: Questions confined to one domain: one job → Work & career, adapting to a new country → Immigration & settling in. 
Children & school: Is the subject related to school? Schools and enrolment, after-school activities, children's workshops. Child-sitting task goes to tasks, work & career
Media & reading: Is the content of Films, series, books, blogs the subject? Television show goes to Culture & entertainment.
Immigration & settling in: Is immigration or settling the subject? The prompt must name it: adaptation, integration, difficulties, obstacles, changes of habit or lifestyle. 
Volunteering & associations covers: Charities and voluntary associations, unpaid community work does not cover: Paid community or sports work → the relevant theme
Health & public services covers: Healthcare systems, doctors, clinics, medication, sick leave
Others: None of the above.

---

## OUTPUT

Return a JSON array. Exactly one object per input prompt, with the same `n`,
in the same order. Do not omit, merge, or add entries.
"""


THEME = Task(
    name="theme",
    answer_key="theme",
    answer_description="exactly one of the theme defined in the rules",
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
         "nouvelles personnes.", "activities for meeting new people"),
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
    "Children & school": [
        "school party",
        "enrollment",
        "after-school activities",
        "child workshop",
        "schools",
    ],
    "Culture & entertainment": [
        "amusement park",
        "library",
        "museums",
        "television show",
        "low-budget cultural activity",
        "toy library",
        "borrowing books",
        "board game club",
        "Canadian evening activities",
        "choosing a film",
        "film festival",
        "New Year celebrations",
        "music festival",
        "artistic activity",
        "bookshop game",
        "concert",
        "show",
        "zoo",
        "cultural festival",
    ],
    "Education & courses": [
        "lessons",
        "university course",
        "community lessons",
        "language stays",
        "continuing studies",
    ],
    "Food & dining": [
        "restaurant",
        "preparing a meal",
        "home cooking services",
    ],
    "Getting to know a person": [
        "personal life",
        "gift",
        "student's activities",
    ],
    "Health & public services": [
        "doctor",
        "healthcare system",
    ],
    "Housing & surroundings": [
        "renting house",
        "shared accommodation",
        "neighborhood",
        "renting out",
        "buying a house",
    ],
    "Immigration & settling in": [
        "settling experience",
        "settling in Canada",
    ],
    "Media & reading": [
        "films",
        "book",
        "series",
        "blog",
        "newspaper"
    ],
    "Shopping & consumer services": [
        "objects selling",
        "smartphone",
        "grocery delivery",
        "shopping options",
        "furniture delivery",
        "used car",
    ],
    "Social life & event": [
        "meeting new people",
        "neighbour gathering",
        "wedding",
        "birthday party",
        "friends visiting",
        "gardening activities",
        "retirement party",
        "outings plateform",
        "restaurant opening event",
        "promotion party",
        "team dinner",
    ],
    "Sports & fitness": [
        "sport club",
        "training sessions",
        "jogging outings",
        "swimming pool",
        "sport",
    ],
    "Tasks, work & career": [
        "work condition",
        "baby-sitting",
        "job",
        "pet-sitting",
        "interview",
        "moving house",
        "remote working setup",
        "delivery",
        "change work",
        "airport pickup",
        "personal shop",
        "after-school childcare employment",
        "house-sitting",
        "company organization",
    ],
    "Transport & mobility": [
        "public transport",
        "carpooling",
        "bicycle rental",
        "commuting by bike",
        "air travel",
        "car rental",
        "roadside assistance",
    ],
    "Travel & tourism": [
        "holiday trip",
        "country trip",
        "city trip",
        "hotel",
        "family trip",
        "cruise",
        "weekend trip",
        "low-budget weekend trip",
        "country trip experience",
        "countryside trip",
        "ski resort holidays",
        "seaside holiday",
        "favorite city",
        "excursion",
        "child's vacation",
        "accommodation options",
    ],
    "Volunteering & associations": [
        "local associations",
        "animal protection association",
        "food aid association",
        "elderly support association",
        "environmental association",
    ],
}


RULES_CORE_SUBJECT = """\
You are annotating TCF Canada Speaking Task 2 role-play prompts (in French).

For each numbered sujet, extract the CORE SUBJECT: the topic that the
candidate must request information about.

METHOD
1. Find the request clause: "vous me demandez / vous me posez des questions /
   vous souhaitez vous renseigner sur ...". The grammatical object of that
   clause is the core subject.
2. IGNORE the interlocutor's role ("Je suis votre voisin(e)...", "Je travaille
   dans...") unless it is the only clue to the topic.
3. IGNORE the parenthetical enumeration (tarifs, horaires, etc.). Those are
   aspects of the subject, never the subject itself.
4. If the scenario is about someone's personal experience of X, the subject is
   X, not "experience".

OUTPUT FORMAT
- A short English noun phrase, 1-4 words.
- No articles, no verbs, no gerund unless unavoidable ("pet-sitting" ok).
- Reuse a label from the CONTROLLED_VOCABULARY below whenever one fits
  semantically, even if the wording differs. Only create a new label if none
  of them fits.
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
        # rule 2: the interlocutor is a travel agent, but the subject is the
        # trip the candidate is asking about, not the agency
        ("Je suis un(e) employe(e) dans une agence de voyages. Vous voulez "
         "faire un voyage touristique. Vous me demandez des informations sur "
         "les destinations (circuits touristiques, tarifs, activites, etc.).",
         "tourist destinations"),
        # rule 4: the friend's experience is the frame; the subject is the
        # thing itself - pet-sitting, not "someone's experience of pet-sitting"
        ("Je suis votre ami(e). Je cherche une personne pour garder mon animal "
         "pendant mes vacances. Vous me posez des questions pour savoir si "
         "vous pouvez le faire (dates, soins, regles, etc.).",
         "pet-sitting"),
    ],
)


TASKS: dict[str, Task] = {t.name: t for t in (THEME, ABSTRACT, CORE_SUBJECT)}
